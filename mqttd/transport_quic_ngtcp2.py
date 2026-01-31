"""
MQTT over QUIC Transport - Phase 3: Core ngtcp2 Integration
Based on curl's curl_ngtcp2.c reference implementation

This implements the core ngtcp2 integration with connection management,
packet processing, stream handling, and TLS integration.

Reference:
- curl/lib/vquic/curl_ngtcp2.c
- curl/lib/vquic/vquic.c
"""

import asyncio
import logging
import socket
import ctypes
import time
import os
import secrets
from ctypes import (
    POINTER, byref, cast, c_void_p, c_int, c_int64, c_uint8, c_uint32, c_uint64,
    c_size_t, c_ssize_t, Structure, Array, create_string_buffer
)
from typing import Optional, Dict, Callable, Any, Tuple, List
from collections import defaultdict
import struct

# Import ngtcp2 bindings
try:
    from .ngtcp2_bindings import (
        NGTCP2_AVAILABLE, get_ngtcp2_lib,
        ngtcp2_cid, ngtcp2_conn, ngtcp2_settings, ngtcp2_transport_params,
        ngtcp2_path, ngtcp2_path_storage, ngtcp2_addr, ngtcp2_conn_callbacks,
        ngtcp2_crypto_conn_ref, SendPacketFunc, RecvPacketFunc,
        ngtcp2_settings_default, ngtcp2_transport_params_default,
        ngtcp2_conn_server_new, ngtcp2_accept, ngtcp2_conn_read_pkt,
        ngtcp2_conn_write_pkt, ngtcp2_conn_handle_expiry, ngtcp2_conn_close,
        ngtcp2_conn_get_expiry, ngtcp2_conn_get_handshake_completed, ngtcp2_conn_del,
        ngtcp2_conn_extend_max_stream_offset, ngtcp2_conn_extend_max_offset,
        ngtcp2_conn_shutdown_stream, ngtcp2_conn_set_stream_user_data,
        ngtcp2_conn_get_stream_user_data,
        ngtcp2_strm_recv, ngtcp2_strm_write,
        NGTCP2_MILLISECONDS, NGTCP2_SECONDS, NGTCP2_MICROSECONDS,
        NGTCP2_MAX_CIDLEN, NGTCP2_PROTO_VER_V1,
    )
except ImportError:
    from mqttd.ngtcp2_bindings import (
        NGTCP2_AVAILABLE, get_ngtcp2_lib,
        ngtcp2_cid, ngtcp2_conn, ngtcp2_settings, ngtcp2_transport_params,
        ngtcp2_path, ngtcp2_path_storage, ngtcp2_addr, ngtcp2_conn_callbacks,
        ngtcp2_crypto_conn_ref, SendPacketFunc, RecvPacketFunc,
        ngtcp2_settings_default, ngtcp2_transport_params_default,
        ngtcp2_conn_server_new, ngtcp2_accept, ngtcp2_conn_read_pkt,
        ngtcp2_conn_write_pkt, ngtcp2_conn_handle_expiry, ngtcp2_conn_close,
        ngtcp2_conn_get_expiry, ngtcp2_conn_get_handshake_completed, ngtcp2_conn_del,
        ngtcp2_conn_extend_max_stream_offset, ngtcp2_conn_extend_max_offset,
        ngtcp2_conn_shutdown_stream, ngtcp2_conn_set_stream_user_data,
        ngtcp2_conn_get_stream_user_data,
        ngtcp2_strm_recv, ngtcp2_strm_write,
        NGTCP2_MILLISECONDS, NGTCP2_SECONDS, NGTCP2_MICROSECONDS,
        NGTCP2_MAX_CIDLEN, NGTCP2_PROTO_VER_V1,
    )

# Import TLS bindings
try:
    from .ngtcp2_tls_bindings import (
        init_tls_backend, verify_tls_bindings,
        USE_OPENSSL, USE_WOLFSSL,
    )
except ImportError:
    from mqttd.ngtcp2_tls_bindings import (
        init_tls_backend, verify_tls_bindings,
        USE_OPENSSL, USE_WOLFSSL,
    )

logger = logging.getLogger(__name__)

# Constants
MAX_PKT_BURST = 10
MAX_UDP_PAYLOAD_SIZE = 1452
QUIC_MAX_STREAMS = 256 * 1024
HANDSHAKE_TIMEOUT = 10 * NGTCP2_SECONDS  # 10 seconds


class NGTCP2Stream:
    """
    Represents a single QUIC stream for MQTT
    
    Based on curl's h3_stream_ctx structure
    """
    
    def __init__(self, stream_id: int, connection: 'NGTCP2Connection'):
        self.stream_id = stream_id
        self.connection = connection
        self.state = "open"  # open, closed, reset
        self.rx_offset = 0
        self.rx_offset_max = 32 * 1024  # Initial window size
        self.send_closed = False
        self.quic_flow_blocked = False
        
        # MQTT data buffer
        self.recv_buffer = bytearray()
        self.send_buffer = bytearray()
        
        # User data (for MQTT handler)
        self.user_data: Optional[Any] = None
    
    def append_data(self, data: bytes, fin: bool = False):
        """Append received stream data"""
        self.recv_buffer.extend(data)
        self.rx_offset += len(data)
        if fin:
            self.state = "closed"
    
    def get_data(self) -> bytes:
        """Get and clear received data"""
        data = bytes(self.recv_buffer)
        self.recv_buffer.clear()
        return data
    
    def has_data(self) -> bool:
        """Check if stream has data to read"""
        return len(self.recv_buffer) > 0
    
    def close(self):
        """Close the stream"""
        self.state = "closed"
        # Check if connection has conn attribute (may be Mock in tests)
        if hasattr(self.connection, 'conn') and self.connection.conn:
            # Shutdown stream in ngtcp2
            try:
                # Use ngtcp2_strm_shutdown (which wraps ngtcp2_conn_shutdown_stream)
                if ngtcp2_strm_shutdown:
                    ngtcp2_strm_shutdown(
                        self.connection.conn,
                        0,  # flags (NGTCP2_SHUTDOWN_STREAM_FLAG_NONE)
                        self.stream_id,
                        0,  # error_code (NO_ERROR)
                    )
            except Exception as e:
                logger.warning(f"Error shutting down stream {self.stream_id}: {e}")


class NGTCP2StreamReader:
    """
    Reader interface for ngtcp2 QUIC stream (compatible with asyncio.StreamReader)
    
    This allows the MQTT handler to work with QUIC streams using the same
    interface as TCP connections.
    """
    
    def __init__(self, stream: NGTCP2Stream):
        self.stream = stream
    
    async def read(self, n: int = -1) -> bytes:
        """Read data from stream"""
        # Wait for data if buffer is empty
        while len(self.stream.recv_buffer) == 0 and self.stream.state != "closed":
            await asyncio.sleep(0.01)
        
        if n == -1:
            data = bytes(self.stream.recv_buffer)
            self.stream.recv_buffer.clear()
            return data
        
        data = bytes(self.stream.recv_buffer[:n])
        self.stream.recv_buffer = self.stream.recv_buffer[n:]
        return data
    
    async def readexactly(self, n: int) -> bytes:
        """Read exactly n bytes"""
        data = b''
        while len(data) < n and self.stream.state != "closed":
            chunk = await self.read(n - len(data))
            if not chunk:
                raise EOFError("Stream closed")
            data += chunk
        return data


class NGTCP2StreamWriter:
    """
    Writer interface for ngtcp2 QUIC stream (compatible with asyncio.StreamWriter)
    
    This allows the MQTT handler to write to QUIC streams using the same
    interface as TCP connections.
    """
    
    def __init__(self, connection: 'NGTCP2Connection', stream: NGTCP2Stream, server: 'QUICServerNGTCP2'):
        self.connection = connection
        self.stream = stream
        self.server = server
    
    def write(self, data: bytes):
        """Write data to QUIC stream"""
        # Add data to send buffer
        self.stream.send_buffer.extend(data)
        
        # Try to send immediately
        # Note: In a full implementation, we'd use ngtcp2_strm_write to write
        # stream data, which would then be sent via ngtcp2_conn_write_pkt
        # For now, we'll trigger a send_packets call
        # Check if connection has conn attribute (may be Mock in tests)
        if hasattr(self.connection, 'conn') and self.connection.conn:
            timestamp = int(time.time() * NGTCP2_SECONDS)
            self.connection.send_packets(timestamp)
    
    async def drain(self):
        """Drain send buffer"""
        await asyncio.sleep(0)  # Yield to event loop
    
    def close(self):
        """Close stream"""
        self.stream.close()
    
    async def wait_closed(self):
        """Wait for stream to close"""
        while self.stream.state != "closed":
            await asyncio.sleep(0.01)
    
    def get_extra_info(self, name: str):
        """Get extra connection info"""
        if name == 'peername':
            return self.connection.remote_addr
        elif name == 'socket':
            return self.connection
        return None


class NGTCP2Connection:
    """
    Represents a single ngtcp2 QUIC connection
    
    Based on curl's cf_ngtcp2_ctx structure
    """
    
    def __init__(
        self,
        server: 'QUICServerNGTCP2',
        dcid: bytes,
        scid: bytes,
        remote_addr: Tuple[str, int],
        path: Optional[ngtcp2_path] = None,
    ):
        self.server = server
        self.dcid = dcid
        self.scid = scid
        self.remote_addr = remote_addr
        self.path = path
        
        # ngtcp2 connection pointer
        self.conn: Optional[ngtcp2_conn] = None
        
        # Connection state
        self.state = "initial"  # initial, handshake, connected, closed
        self.handshake_completed = False
        
        # Streams (stream_id -> NGTCP2Stream)
        self.streams: Dict[int, NGTCP2Stream] = {}
        self.next_stream_id = 0  # For server-initiated streams
        
        # Timestamps
        self.created_at = time.time()
        self.last_packet_at = time.time()
        self.last_io_at = time.time()
        
        # Path storage
        self.path_storage = ngtcp2_path_storage()
        
        # Settings and transport params
        self.settings = ngtcp2_settings()
        self.transport_params = ngtcp2_transport_params()
        
        # Callbacks
        self.callbacks = ngtcp2_conn_callbacks()
        
        # TLS context (will be set up later)
        self.tls_ctx: Optional[c_void_p] = None
        
        # User data for callbacks
        self.user_data_ptr = c_void_p(id(self))
        
        # Statistics
        self.packets_sent = 0
        self.packets_received = 0
        self.bytes_sent = 0
        self.bytes_received = 0
    
    def initialize(self) -> bool:
        """Initialize ngtcp2 connection"""
        try:
            # CRITICAL: Ensure TLS backend is initialized before any ngtcp2 calls
            # This must happen before ngtcp2_settings_default or any other ngtcp2 functions
            if not self.server.tls_initialized:
                from .ngtcp2_tls_bindings import init_tls_backend
                if not init_tls_backend():
                    logger.error("Failed to initialize TLS backend - ngtcp2 will crash")
                    return False
                self.server.tls_initialized = True
            
            # Initialize settings with defaults
            # WORKAROUND: ngtcp2_settings_default crashes in some configurations
            # Manually initialize settings instead to avoid crash
            # This is safe because we're setting the same defaults that ngtcp2 would set
            try:
                if ngtcp2_settings_default:
                    ngtcp2_settings_default(byref(self.settings))
            except (SystemError, OSError, RuntimeError, AttributeError) as e:
                # If settings_default crashes or is None, manually initialize
                logger.debug(f"ngtcp2_settings_default not available, using manual initialization: {e}")
                # Manually set defaults (minimal required fields)
                # Zero out the structure first
                import ctypes
                ctypes.memset(ctypes.byref(self.settings), 0, ctypes.sizeof(self.settings))
                # Set basic required fields (matching ngtcp2 defaults)
                self.settings.cc_algo = 0  # NGTCP2_CC_ALGO_CUBIC
                self.settings.initial_rtt = 333000  # 333ms (NGTCP2_DEFAULT_INITIAL_RTT)
                self.settings.ack_thresh = 2
                self.settings.max_tx_udp_payload_size = 1452  # 1500 - 48 (UDP header)
                self.settings.handshake_timeout = HANDSHAKE_TIMEOUT
                self.settings.max_window = 100 * 1024 * 1024  # 100 MB
                self.settings.max_stream_window = 10 * 1024 * 1024  # 10 MB
            
            # Set custom settings
            self.settings.initial_ts = int(time.time() * NGTCP2_SECONDS)
            self.settings.handshake_timeout = HANDSHAKE_TIMEOUT
            self.settings.max_window = 100 * 1024 * 1024  # 100 MB
            self.settings.max_stream_window = 10 * 1024 * 1024  # 10 MB
            
            # Initialize transport params with defaults
            if ngtcp2_transport_params_default:
                ngtcp2_transport_params_default(byref(self.transport_params))
            
            # Set transport params
            self.transport_params.initial_max_data = self.settings.max_window
            self.transport_params.initial_max_stream_data_bidi_local = 32 * 1024
            self.transport_params.initial_max_stream_data_bidi_remote = 32 * 1024
            self.transport_params.initial_max_stream_data_uni = self.settings.max_window
            self.transport_params.initial_max_streams_bidi = QUIC_MAX_STREAMS
            self.transport_params.initial_max_streams_uni = QUIC_MAX_STREAMS
            self.transport_params.max_idle_timeout = 0  # No idle timeout
            
            # Set original_dcid for server (from client's first Initial packet)
            dcid_cid = ngtcp2_cid(self.dcid)
            self.transport_params.original_dcid = dcid_cid
            self.transport_params.original_dcid_present = 1
            
            # Initialize path storage
            # TODO: Set up proper path from remote_addr
            
            # Set up callbacks
            self._setup_callbacks()
            
            # Create connection
            conn_ptr = POINTER(ngtcp2_conn)()
            scid_cid = ngtcp2_cid(self.scid)
            
            # Create server connection
            # Note: ngtcp2_conn_server_new may be a wrapper function
            result = ngtcp2_conn_server_new(
                byref(conn_ptr),  # conn (out)
                byref(dcid_cid),  # dcid
                byref(scid_cid),  # scid
                byref(self.path_storage.ps) if self.path else None,  # path
                NGTCP2_PROTO_VER_V1,  # client_chosen_version
                byref(self.callbacks),  # callbacks
                byref(self.settings),  # settings
                byref(self.transport_params),  # transport_params
                None,  # mem (memory allocator, can be NULL)
                self.user_data_ptr,  # user_data
            )
            
            if result != 0:
                logger.error(f"Failed to create ngtcp2 connection: {result}")
                return False
            
            self.conn = conn_ptr.contents if conn_ptr else None
            if not self.conn:
                logger.error("ngtcp2_conn_server_new returned NULL")
                return False
            
            self.state = "handshake"
            logger.info(f"Created ngtcp2 connection: dcid={self.dcid.hex()[:8]}, scid={self.scid.hex()[:8]}")
            return True
            
        except Exception as e:
            logger.error(f"Error initializing ngtcp2 connection: {e}", exc_info=True)
            return False
    
    def _setup_callbacks(self):
        """Set up ngtcp2 connection callbacks"""
        # Note: This is a simplified version. Full implementation would
        # set all callback function pointers in the callbacks structure.
        # For now, we'll handle callbacks via Python wrapper functions.
        pass
    
    def recv_packet(self, data: bytes, timestamp: Optional[int] = None) -> bool:
        """
        Receive and process a QUIC packet
        
        Based on curl's cf_ngtcp2_recv_pkts and cf_progress_ingress
        """
        if not self.conn:
            return False
        
        try:
            if timestamp is None:
                timestamp = int(time.time() * NGTCP2_SECONDS)
            
            # Convert data to ctypes
            pkt_data = (c_uint8 * len(data)).from_buffer_copy(data)
            
            # Read packet into connection
            result = ngtcp2_conn_read_pkt(
                self.conn,
                byref(self.path_storage.ps) if self.path else None,
                None,  # pkt_info (can be NULL)
                pkt_data,
                len(data),
                timestamp,
            )
            
            if result != 0:
                logger.warning(f"ngtcp2_conn_read_pkt returned error: {result}")
                return False
            
            self.packets_received += 1
            self.bytes_received += len(data)
            self.last_packet_at = time.time()
            self.last_io_at = time.time()
            
            # Check if handshake completed
            if ngtcp2_conn_get_handshake_completed:
                if ngtcp2_conn_get_handshake_completed(self.conn):
                    if not self.handshake_completed:
                        self.handshake_completed = True
                        self.state = "connected"
                        logger.info(f"Handshake completed for connection {self.dcid.hex()[:8]}")
                        
                        # Start stream processing task for MQTT
                        asyncio.create_task(self._process_streams())
            
            # Process stream data from packet
            self._extract_stream_data()
            
            return True
            
        except Exception as e:
            logger.error(f"Error receiving packet: {e}", exc_info=True)
            return False
    
    def send_packets(self, timestamp: Optional[int] = None) -> bool:
        """
        Send pending packets
        
        Based on curl's cf_progress_egress
        """
        if not self.conn:
            return False
        
        try:
            if timestamp is None:
                timestamp = int(time.time() * NGTCP2_SECONDS)
            
            # Write packets (ngtcp2 will call our send callback)
            # We need to call ngtcp2_conn_write_pkt in a loop until no more packets
            max_packets = MAX_PKT_BURST
            packets_sent = 0
            
            while packets_sent < max_packets:
                # Allocate buffer for packet
                pkt_buf = (c_uint8 * MAX_UDP_PAYLOAD_SIZE)()
                pktlen = c_size_t(0)
                
                # Write packet
                result = ngtcp2_conn_write_pkt(
                    self.conn,
                    byref(self.path_storage.ps) if self.path else None,
                    pkt_buf,
                    MAX_UDP_PAYLOAD_SIZE,
                    byref(pktlen),
                    timestamp,
                    self.user_data_ptr,
                    None,  # send_pkt callback (handled via Python)
                )
                
                if result != 0:
                    # No more packets to send or error
                    break
                
                if pktlen.value > 0:
                    # Send packet via UDP
                    pkt_data = bytes(pkt_buf[:pktlen.value])
                    self.server.send_packet(pkt_data, self.remote_addr)
                    packets_sent += 1
                    self.packets_sent += 1
                    self.bytes_sent += pktlen.value
                    self.last_io_at = time.time()
                else:
                    break
            
            return True
            
        except Exception as e:
            logger.error(f"Error sending packets: {e}", exc_info=True)
            return False
    
    def handle_expiry(self, timestamp: Optional[int] = None) -> bool:
        """
        Handle connection expiry/timeouts
        
        Based on curl's check_and_set_expiry
        """
        if not self.conn:
            return False
        
        try:
            if timestamp is None:
                timestamp = int(time.time() * NGTCP2_SECONDS)
            
            # Get expiry time
            if ngtcp2_conn_get_expiry:
                expiry = ngtcp2_conn_get_expiry(self.conn)
                if expiry != 0xFFFFFFFFFFFFFFFF:  # UINT64_MAX
                    if expiry <= timestamp:
                        # Handle expiry
                        result = ngtcp2_conn_handle_expiry(
                            self.conn,
                            byref(self.path_storage.ps) if self.path else None,
                            timestamp,
                            self.user_data_ptr,
                            None,  # send_pkt callback
                        )
                        if result != 0:
                            logger.warning(f"ngtcp2_conn_handle_expiry returned error: {result}")
                            return False
                        
                        # Try to send packets after handling expiry
                        self.send_packets(timestamp)
            
            return True
            
        except Exception as e:
            logger.error(f"Error handling expiry: {e}", exc_info=True)
            return False
    
    def get_stream(self, stream_id: int) -> Optional[NGTCP2Stream]:
        """Get or create a stream"""
        if stream_id not in self.streams:
            self.streams[stream_id] = NGTCP2Stream(stream_id, self)
        return self.streams[stream_id]
    
    def _extract_stream_data(self):
        """
        Extract stream data from processed packets
        
        Note: In a full implementation, this would use ngtcp2 callbacks
        to receive stream data. For Phase 4, we use a simplified approach
        where stream data is tracked manually or through callbacks.
        """
        # This is a placeholder. In a full implementation, stream data
        # would come through ngtcp2 callbacks (recv_stream_data callback).
        # For now, we'll process streams in _process_streams task.
        pass
    
    async def _process_streams(self):
        """
        Process stream data and trigger MQTT handler
        
        This task runs after handshake completes to process
        incoming stream data and handle MQTT messages.
        """
        while self.state == "connected" and self.conn:
            try:
                # Check all streams for data
                for stream_id, stream in list(self.streams.items()):
                    if stream.has_data() and self.server.mqtt_handler:
                        # Process MQTT data on this stream
                        await self.server._handle_mqtt_over_quic(self, stream)
                
                await asyncio.sleep(0.01)  # Check every 10ms
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error processing streams: {e}", exc_info=True)
                await asyncio.sleep(0.1)
    
    def close(self, error_code: int = 0):
        """Close the connection"""
        if not self.conn:
            return
        
        try:
            timestamp = int(time.time() * NGTCP2_SECONDS)
            if ngtcp2_conn_close:
                # ngtcp2_conn_close is a wrapper that handles packet sending
                result = ngtcp2_conn_close(
                    self.conn,
                    byref(self.path_storage.ps) if self.path else None,
                    error_code,
                    None,  # reason (not used in wrapper)
                    0,  # reasonlen
                    timestamp,
                    self.user_data_ptr,
                    None,  # send_pkt callback (handled in wrapper)
                )
                if result == 0:
                    self.state = "closed"
                    logger.info(f"Closed connection {self.dcid.hex()[:8]}")
                else:
                    logger.warning(f"Connection close returned error: {result}")
            else:
                # Fallback: just mark as closed
                self.state = "closed"
                logger.info(f"Closed connection {self.dcid.hex()[:8]} (ngtcp2_conn_close not available)")
        except Exception as e:
            logger.error(f"Error closing connection: {e}", exc_info=True)
            self.state = "closed"
    
    def cleanup(self):
        """Clean up connection resources"""
        # Close all streams
        for stream in list(self.streams.values()):
            stream.close()
        self.streams.clear()
        
        # Delete ngtcp2 connection
        if self.conn and ngtcp2_conn_del:
            try:
                ngtcp2_conn_del(self.conn, None)  # mem = NULL
            except Exception as e:
                logger.warning(f"Error deleting ngtcp2 connection: {e}")
        self.conn = None
        self.state = "closed"


class QUICServerNGTCP2:
    """
    MQTT over QUIC Server using ngtcp2 (Phase 3: Core Integration)
    
    Based on curl's cf_ngtcp2_ctx structure and implementation patterns.
    """
    
    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 1884,
        certfile: Optional[str] = None,
        keyfile: Optional[str] = None,
    ):
        if not NGTCP2_AVAILABLE:
            raise RuntimeError(
                "ngtcp2 library not available. "
                "Install ngtcp2: https://github.com/ngtcp2/ngtcp2"
            )
        
        self.host = host
        self.port = port
        self.certfile = certfile
        self.keyfile = keyfile
        
        # UDP socket
        self.sock: Optional[socket.socket] = None
        self.transport: Optional[asyncio.DatagramTransport] = None
        
        # Connections (by DCID hash or connection ID)
        self.connections: Dict[bytes, NGTCP2Connection] = {}
        
        # MQTT handler
        self.mqtt_handler: Optional[Callable] = None
        
        # Statistics
        self.packets_received = 0
        self.packets_sent = 0
        
        # TLS initialization flag - will be set when first connection initializes
        self.tls_initialized = False
        
        # Initialize TLS backend if available
        # Note: This is a warning, not an error, as TLS setup may happen later
        # Skip TLS initialization in test environments to avoid crashes
        skip_tls_init = os.environ.get('MQTTD_SKIP_TLS_INIT', '0') == '1'
        
        if not skip_tls_init:
            try:
                # Force reload crypto library to ensure it's available
                from .ngtcp2_tls_bindings import (
                    _load_ngtcp2_crypto_library, init_tls_backend,
                    NGTCP2_CRYPTO_AVAILABLE, USE_OPENSSL
                )
                # Ensure crypto library is loaded
                _load_ngtcp2_crypto_library()
                if init_tls_backend():
                    logger.info("TLS backend initialized")
                    self.tls_initialized = True
                else:
                    logger.warning("TLS backend not available - QUIC will not work without TLS")
                    # Don't set tls_initialized = False here - let first connection try
            except Exception as e:
                # Don't crash if TLS backend initialization fails
                # This can happen in test environments or when ngtcp2 is not fully configured
                logger.warning(f"TLS backend initialization failed: {e} - QUIC will not work without TLS")
        else:
            logger.debug("Skipping TLS backend initialization (MQTTD_SKIP_TLS_INIT=1)")
    
    def set_mqtt_handler(self, handler: Callable):
        """Set MQTT connection handler"""
        self.mqtt_handler = handler
    
    async def start(self):
        """Start QUIC server"""
        loop = asyncio.get_event_loop()
        
        # Create UDP socket
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 2 * 1024 * 1024)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 2 * 1024 * 1024)
        self.sock.bind((self.host, self.port))
        self.sock.setblocking(False)
        
        # Create datagram transport
        self.transport, protocol = await loop.create_datagram_endpoint(
            lambda: QUICServerProtocolNGTCP2(self),
            sock=self.sock
        )
        
        # Start connection maintenance task
        asyncio.create_task(self._connection_maintenance())
        
        logger.info(f"MQTT over QUIC server (ngtcp2) listening on {self.host}:{self.port} (UDP)")
    
    async def stop(self):
        """Stop QUIC server"""
        # Close all connections
        for conn in list(self.connections.values()):
            conn.close()
            conn.cleanup()
        self.connections.clear()
        
        if self.transport:
            self.transport.close()
        if self.sock:
            self.sock.close()
        logger.info("QUIC server (ngtcp2) stopped")
    
    async def _connection_maintenance(self):
        """Periodic connection maintenance (expiry, timeouts)"""
        while self.transport and not self.transport.is_closing():
            try:
                timestamp = int(time.time() * NGTCP2_SECONDS)
                
                # Handle expiry for all connections
                for conn in list(self.connections.values()):
                    conn.handle_expiry(timestamp)
                    conn.send_packets(timestamp)
                
                # Clean up closed connections
                to_remove = []
                for dcid, conn in self.connections.items():
                    if conn.state == "closed":
                        to_remove.append(dcid)
                
                for dcid in to_remove:
                    conn = self.connections.pop(dcid)
                    conn.cleanup()
                
                await asyncio.sleep(0.01)  # 10ms maintenance interval
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in connection maintenance: {e}", exc_info=True)
                await asyncio.sleep(0.1)
    
    def handle_packet(self, data: bytes, addr: Tuple[str, int]):
        """
        Handle incoming QUIC packet
        
        Based on curl's cf_ngtcp2_recv_pkts
        """
        self.packets_received += 1
        
        try:
            # Parse packet header to get DCID
            # For Initial packets, we need to accept the connection
            if len(data) < 1:
                return
            
            # Try to decode version and CID (simplified)
            # Full implementation would use ngtcp2_pkt_decode_version_cid
            dcid = self._extract_dcid(data)
            
            if not dcid:
                logger.debug("Could not extract DCID from packet")
                return
            
            # Find or create connection
            conn = self.connections.get(dcid)
            
            if not conn:
                # Check if this is an Initial packet (new connection)
                if self._is_initial_packet(data):
                    # Accept new connection
                    if ngtcp2_accept:
                        # Create new connection
                        scid = secrets.token_bytes(8)  # Generate server CID
                        conn = NGTCP2Connection(self, dcid, scid, addr)
                        if conn.initialize():
                            self.connections[dcid] = conn
                            logger.info(f"Accepted new connection from {addr}")
                        else:
                            logger.error("Failed to initialize new connection")
                            return
                    else:
                        logger.warning("ngtcp2_accept not available")
                        return
                else:
                    # Unknown connection, drop packet
                    logger.debug(f"Dropping packet for unknown connection: {dcid.hex()[:8]}")
                    return
            
            # Process packet in connection
            timestamp = int(time.time() * NGTCP2_SECONDS)
            if conn.recv_packet(data, timestamp):
                # Try to send any pending packets
                conn.send_packets(timestamp)
            else:
                logger.warning(f"Failed to process packet for connection {dcid.hex()[:8]}")
                
        except Exception as e:
            logger.error(f"Error handling packet: {e}", exc_info=True)
    
    def _extract_dcid(self, data: bytes) -> Optional[bytes]:
        """Extract Destination Connection ID from packet (simplified)"""
        # This is a simplified version. Full implementation would use
        # ngtcp2_pkt_decode_version_cid or parse QUIC header properly.
        if len(data) < 20:
            return None
        
        # For Initial packets, DCID is at offset 5-20 (simplified)
        # Real implementation needs proper QUIC header parsing
        try:
            # Check if it's an Initial packet (first byte has specific flags)
            if (data[0] & 0x80) == 0x80:  # Long header
                if (data[0] & 0x30) == 0x00:  # Initial packet
                    dcid_len = data[5] if len(data) > 5 else 0
                    if dcid_len > 0 and dcid_len <= NGTCP2_MAX_CIDLEN:
                        if len(data) >= 6 + dcid_len:
                            return data[6:6+dcid_len]
        except Exception:
            pass
        
        return None
    
    def _is_initial_packet(self, data: bytes) -> bool:
        """Check if packet is an Initial packet"""
        if len(data) < 1:
            return False
        # Long header with Initial packet type
        return (data[0] & 0x80) == 0x80 and (data[0] & 0x30) == 0x00
    
    def send_packet(self, data: bytes, addr: Tuple[str, int]):
        """Send QUIC packet via UDP"""
        if self.transport and not self.transport.is_closing():
            try:
                self.transport.sendto(data, addr)
                self.packets_sent += 1
            except Exception as e:
                logger.error(f"Error sending packet: {e}")
    
    async def _handle_mqtt_over_quic(self, connection: NGTCP2Connection, stream: NGTCP2Stream):
        """
        Handle MQTT data received over QUIC stream
        
        This creates reader/writer interfaces compatible with the MQTT handler,
        allowing reuse of existing MQTT processing code.
        """
        if not self.mqtt_handler:
            return
        
        try:
            # Create reader/writer interfaces for MQTT handler
            # This allows reusing existing MQTT handling code
            reader = NGTCP2StreamReader(stream)
            writer = NGTCP2StreamWriter(connection, stream, self)
            
            # Call MQTT handler (same interface as TCP: reader, writer only)
            # Connection available via writer.get_extra_info('socket')
            await self.mqtt_handler(reader, writer)
            
        except Exception as e:
            logger.error(f"Error handling MQTT over QUIC: {e}", exc_info=True)


class QUICServerProtocolNGTCP2(asyncio.DatagramProtocol):
    """UDP protocol handler for QUIC with ngtcp2"""
    
    def __init__(self, server: QUICServerNGTCP2):
        self.server = server
    
    def datagram_received(self, data: bytes, addr: Tuple[str, int]):
        """Handle incoming UDP datagram"""
        self.server.handle_packet(data, addr)
    
    def error_received(self, exc: Exception):
        """Handle UDP error"""
        logger.error(f"UDP error: {exc}")


# Export availability flag
NGTCP2_AVAILABLE = NGTCP2_AVAILABLE

# Export
__all__ = ['QUICServerNGTCP2', 'NGTCP2Connection', 'NGTCP2Stream', 'NGTCP2_AVAILABLE']
