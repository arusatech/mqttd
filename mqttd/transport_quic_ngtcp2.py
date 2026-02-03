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
import threading
import time
import os
import secrets
from ctypes import (
    POINTER, byref, cast, c_void_p, c_int, c_int64, c_uint8, c_uint32, c_uint64,
    c_size_t, c_ssize_t, Structure, Array, create_string_buffer, CFUNCTYPE
)
from typing import Optional, Dict, Callable, Any, Tuple, List
from collections import defaultdict
import struct

# Import ngtcp2 bindings
try:
    from .ngtcp2_bindings import (
        NGTCP2_AVAILABLE, get_ngtcp2_lib,
        ngtcp2_cid, ngtcp2_conn, ngtcp2_settings, ngtcp2_transport_params,
        ngtcp2_path, ngtcp2_path_storage, ngtcp2_addr, ngtcp2_sockaddr_in,
        ngtcp2_conn_callbacks, ngtcp2_crypto_conn_ref, SendPacketFunc, RecvPacketFunc,
        ngtcp2_vec,
        ngtcp2_settings_default, ngtcp2_transport_params_default,
        ngtcp2_path_storage_init,
        ngtcp2_conn_server_new, ngtcp2_accept, ngtcp2_conn_read_pkt,
        ngtcp2_conn_write_pkt, ngtcp2_conn_handle_expiry, ngtcp2_conn_close,
        ngtcp2_conn_get_expiry, ngtcp2_conn_get_timestamp, ngtcp2_conn_get_handshake_completed, ngtcp2_conn_del,
        ngtcp2_conn_get_remote_transport_params, ngtcp2_conn_get_path,
        ngtcp2_conn_force_validate_path, ngtcp2_path_is_local_network,
        ngtcp2_conn_extend_max_stream_offset, ngtcp2_conn_extend_max_offset,
        ngtcp2_conn_shutdown_stream, ngtcp2_conn_set_stream_user_data,
        ngtcp2_conn_get_stream_user_data,
        ngtcp2_conn_get_tls_alert,
        ngtcp2_conn_set_keep_alive_timeout,
        ngtcp2_strm_recv, ngtcp2_strm_write, ngtcp2_strm_shutdown,
        ngtcp2_conn_submit_stream_data, ngtcp2_conn_writev_stream_versioned,
        NGTCP2_MILLISECONDS, NGTCP2_SECONDS, NGTCP2_MICROSECONDS,
        NGTCP2_MAX_CIDLEN, NGTCP2_PROTO_VER_V1, NGTCP2_MAX_UDP_PAYLOAD_SIZE,
        NGTCP2_DEFAULT_MAX_RECV_UDP_PAYLOAD_SIZE,
        NGTCP2_DEFAULT_ACK_DELAY_EXPONENT, NGTCP2_DEFAULT_MAX_ACK_DELAY,
        NGTCP2_DEFAULT_ACTIVE_CONNECTION_ID_LIMIT,
        NGTCP2_PKT_INFO_V1,
    )
except ImportError:
    from mqttd.ngtcp2_bindings import (
        NGTCP2_AVAILABLE, get_ngtcp2_lib,
        ngtcp2_cid, ngtcp2_conn, ngtcp2_settings, ngtcp2_transport_params,
        ngtcp2_path, ngtcp2_path_storage, ngtcp2_addr, ngtcp2_sockaddr_in,
        ngtcp2_conn_callbacks, ngtcp2_crypto_conn_ref, SendPacketFunc, RecvPacketFunc,
        ngtcp2_settings_default, ngtcp2_transport_params_default,
        ngtcp2_path_storage_init,
        ngtcp2_conn_server_new, ngtcp2_accept, ngtcp2_conn_read_pkt,
        ngtcp2_conn_write_pkt, ngtcp2_conn_handle_expiry, ngtcp2_conn_close,
        ngtcp2_conn_get_expiry, ngtcp2_conn_get_timestamp, ngtcp2_conn_get_handshake_completed, ngtcp2_conn_del,
        ngtcp2_conn_get_remote_transport_params, ngtcp2_conn_get_path,
        ngtcp2_conn_force_validate_path, ngtcp2_path_is_local_network,
        ngtcp2_conn_extend_max_stream_offset, ngtcp2_conn_extend_max_offset,
        ngtcp2_conn_shutdown_stream, ngtcp2_conn_set_stream_user_data,
        ngtcp2_conn_get_stream_user_data,
        ngtcp2_conn_get_tls_alert,
        ngtcp2_conn_set_keep_alive_timeout,
        ngtcp2_strm_recv, ngtcp2_strm_write, ngtcp2_strm_shutdown,
        ngtcp2_conn_submit_stream_data, ngtcp2_conn_writev_stream_versioned,
        NGTCP2_MILLISECONDS, NGTCP2_SECONDS, NGTCP2_MICROSECONDS,
        NGTCP2_MAX_CIDLEN, NGTCP2_PROTO_VER_V1, NGTCP2_MAX_UDP_PAYLOAD_SIZE,
        NGTCP2_DEFAULT_MAX_RECV_UDP_PAYLOAD_SIZE,
        NGTCP2_DEFAULT_ACK_DELAY_EXPONENT, NGTCP2_DEFAULT_MAX_ACK_DELAY,
        NGTCP2_DEFAULT_ACTIVE_CONNECTION_ID_LIMIT,
        NGTCP2_PKT_INFO_V1,
    )

# Import TLS bindings
try:
    from .ngtcp2_tls_bindings import (
        init_tls_backend, verify_tls_bindings,
        USE_OPENSSL, USE_WOLFSSL,
        _ensure_callbacks_bound, _ensure_openssl_bound,
        create_server_tls_ctx, create_server_tls_session, free_tls_session,
        SSL_set_app_data,
    )
    from . import ngtcp2_tls_bindings as _tls_mod
    from .ngtcp2_bindings import ngtcp2_conn_set_tls_native_handle
except ImportError:
    from mqttd.ngtcp2_tls_bindings import (
        init_tls_backend, verify_tls_bindings,
        USE_OPENSSL, USE_WOLFSSL,
        _ensure_callbacks_bound, _ensure_openssl_bound,
        create_server_tls_ctx, create_server_tls_session, free_tls_session,
        SSL_set_app_data,
    )
    from mqttd import ngtcp2_tls_bindings as _tls_mod
    from mqttd.ngtcp2_bindings import ngtcp2_conn_set_tls_native_handle

logger = logging.getLogger(__name__)

# Map id(self) -> NGTCP2Connection for callbacks
_CONN_REGISTRY: Dict[int, "NGTCP2Connection"] = {}

# Constants
MAX_PKT_BURST = 10
MAX_UDP_PAYLOAD_SIZE = 1452
QUIC_MAX_STREAMS = 256 * 1024
HANDSHAKE_TIMEOUT = 10 * NGTCP2_SECONDS  # 10 seconds
# ngtcp2 stream data flags (from ngtcp2.h)
NGTCP2_STREAM_DATA_FLAG_FIN = 0x01

# ngtcp2 read_pkt error code -> human-readable message (from ngtcp2.h)
_NGTCP2_READ_PKT_ERRORS = {
    -225: "NGTCP2_ERR_TRANSPORT_PARAM (transport parameter error)",
    -216: "NGTCP2_ERR_MALFORMED_TRANSPORT_PARAM (malformed transport parameter)",
    -215: "NGTCP2_ERR_REQUIRED_TRANSPORT_PARAM (required transport parameter missing)",
}

# handle_expiry return codes: treat as connection close (log once, mark closed)
NGTCP2_ERR_HANDSHAKE_TIMEOUT = -236
NGTCP2_ERR_IDLE_CLOSE = -238


def _ngtcp2_read_pkt_error_message(result: int) -> str:
    """Return a human-readable message for ngtcp2_conn_read_pkt error code."""
    return _NGTCP2_READ_PKT_ERRORS.get(result, str(result))


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
        # Check if connection has conn pointer (may be Mock in tests)
        if hasattr(self.connection, '_conn_ptr') and self.connection._conn_ptr:
            # Shutdown stream in ngtcp2
            try:
                # Use ngtcp2_strm_shutdown (which wraps ngtcp2_conn_shutdown_stream)
                if ngtcp2_strm_shutdown:
                    ngtcp2_strm_shutdown(
                        self.connection._conn_ptr,
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
            timestamp = time.monotonic_ns()
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
        client_scid: bytes,
        scid: bytes,
        remote_addr: Tuple[str, int],
        path: Optional[ngtcp2_path] = None,
    ):
        self.server = server
        self.dcid = dcid  # DCID from packet (our ID) - used for connection map key and original_dcid
        self.client_scid = client_scid  # SCID from packet (client's ID) - must be passed as dcid to ngtcp2_conn_server_new so initial_scid validation passes
        self.scid = scid  # our new server-generated SCID
        self.remote_addr = remote_addr
        self.path = path
        # Track all connection IDs that should map to this connection
        self.cids = set()
        _CONN_REGISTRY[id(self)] = self

        # Placeholder for stream packet callback (not used with current API)
        self._send_pkt_cb = None
        
        # ngtcp2 connection pointer
        self.conn: Optional[ngtcp2_conn] = None
        self._conn_ptr = None  # Keep the ctypes pointer reference
        
        # Connection state
        self.state = "initial"  # initial, handshake, connected, closed
        self.handshake_completed = False
        self._remote_idle_overridden = False
        
        # Streams (stream_id -> NGTCP2Stream)
        self.streams: Dict[int, NGTCP2Stream] = {}
        self.next_stream_id = 0  # For server-initiated streams
        
        # Timestamps
        self.created_at = time.time()
        self.last_packet_at = time.time()
        self.last_io_at = time.time()
        
        # Path storage (must be valid for ngtcp2_conn_server_new - path must not be NULL)
        self.path_storage = ngtcp2_path_storage()
        # Keep sockaddr buffers alive so path_storage.ps pointers stay valid
        self._local_sockaddr = ngtcp2_sockaddr_in()
        self._remote_sockaddr = ngtcp2_sockaddr_in()
        
        # Settings and transport params
        self.settings = ngtcp2_settings()
        self.transport_params = ngtcp2_transport_params()
        
        # Callbacks
        self.callbacks = ngtcp2_conn_callbacks()
        
        # TLS context (will be set up later)
        self.tls_ctx: Optional[c_void_p] = None
        # TLS session for this connection (OpenSSL 3.x implements TLS 1.3)
        self.tls_session: Optional[c_void_p] = None
        # Crypto connection reference (for ngtcp2_crypto callbacks to find conn)
        self.crypto_conn_ref: Optional[ngtcp2_crypto_conn_ref] = None
        # Keep reference to get_conn callback to prevent GC
        self._get_conn_callback = None
        
        # User data for callbacks
        self.user_data_ptr = c_void_p(id(self))
        
        # Lock: serialize read_pkt / write_pkt so timestamp (get_timestamp + write_pkt) is never interleaved with read_pkt
        self._ts_lock = threading.Lock()
        
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
            # WORKAROUND: Never call C ngtcp2_settings_default - it can trigger
            # ngtcp2_settingslen_version(version) with invalid version and abort.
            # Always use manual initialization (same defaults as ngtcp2).
            self.settings.cc_algo = 0  # NGTCP2_CC_ALGO_CUBIC
            self.settings.initial_rtt = 333000  # 333ms (NGTCP2_DEFAULT_INITIAL_RTT)
            self.settings.ack_thresh = 2
            # Default to safe minimum; we may raise for trusted local networks later.
            self.settings.max_tx_udp_payload_size = NGTCP2_MAX_UDP_PAYLOAD_SIZE  # ngtcp2_conn.c assertion
            self.settings.handshake_timeout = HANDSHAKE_TIMEOUT
            self.settings.max_window = 100 * 1024 * 1024  # 100 MB
            self.settings.max_stream_window = 10 * 1024 * 1024  # 10 MB
            # Allow sending before dcid->max_udp_payload_size is set (0 before first send); otherwise conn_shape_udp_payload caps destlen to 0 and write_pkt returns 0.
            self.settings.no_tx_udp_payload_size_shaping = 1
            
            # Set custom settings: ngtcp2 requires monotonic timestamps (nanoseconds); use monotonic_ns()
            self.settings.initial_ts = time.monotonic_ns()
            self._last_ts = self.settings.initial_ts  # ngtcp2 requires conn->log.last_ts <= ts
            self.settings.handshake_timeout = HANDSHAKE_TIMEOUT
            self.settings.max_window = 100 * 1024 * 1024  # 100 MB
            self.settings.max_stream_window = 10 * 1024 * 1024  # 10 MB
            
            # Initialize transport params with C library defaults (max_udp_payload_size, ack_delay_exponent, max_ack_delay, active_connection_id_limit)
            if ngtcp2_transport_params_default:
                ngtcp2_transport_params_default(byref(self.transport_params))
            
            # Set transport params (overrides)
            self.transport_params.initial_max_data = self.settings.max_window
            self.transport_params.initial_max_stream_data_bidi_local = 32 * 1024
            self.transport_params.initial_max_stream_data_bidi_remote = 32 * 1024
            self.transport_params.initial_max_stream_data_uni = self.settings.max_window
            self.transport_params.initial_max_streams_bidi = QUIC_MAX_STREAMS
            self.transport_params.initial_max_streams_uni = QUIC_MAX_STREAMS
            # Use 30s idle timeout (ngtcp2 uses ns).
            self.transport_params.max_idle_timeout = 30 * NGTCP2_SECONDS
            # active_connection_id_limit in [2, 7]; keep 2 from default
            self.transport_params.active_connection_id_limit = 2
            # Ensure ngtcp2-valid defaults for params checked during handshake (in case default init was fallback)
            self.transport_params.max_udp_payload_size = NGTCP2_DEFAULT_MAX_RECV_UDP_PAYLOAD_SIZE
            self.transport_params.ack_delay_exponent = NGTCP2_DEFAULT_ACK_DELAY_EXPONENT
            self.transport_params.max_ack_delay = NGTCP2_DEFAULT_MAX_ACK_DELAY
            
            # Set original_dcid for server (DCID from client's Initial packet = our connection ID)
            original_dcid_cid = ngtcp2_cid(self.dcid)
            self.transport_params.original_dcid = original_dcid_cid
            self.transport_params.original_dcid_present = 1
            # Server must not send initial_scid/retry_scid in normal connection
            self.transport_params.initial_scid_present = 0
            self.transport_params.retry_scid_present = 0
            # Do not send version_information unless doing version negotiation
            self.transport_params.version_info_present = 0
            self.transport_params.version_info.chosen_version = 0
            self.transport_params.version_info.available_versions = None
            self.transport_params.version_info.available_versionslen = 0
            
            # Initialize path: ngtcp2_conn_server_new requires a non-NULL path
            # (crash in ngtcp2_addr_copy if path is NULL)
            self._fill_path_storage()

            # For non-local/public networks, keep max_tx_udp_payload_size at 1200 to avoid MTU drops.
            # For local networks we can use larger payloads if desired.
            if ngtcp2_path_is_local_network and ngtcp2_path_is_local_network(byref(self.path_storage.path)):
                # Allow larger payloads on local LAN if needed
                self.settings.max_tx_udp_payload_size = max(1452, NGTCP2_MAX_UDP_PAYLOAD_SIZE)
            else:
                # Advertise conservative max_udp_payload_size to client for WAN path
                self.transport_params.max_udp_payload_size = NGTCP2_MAX_UDP_PAYLOAD_SIZE
            
            # Set up callbacks
            self._setup_callbacks()
            
            # Create connection
            conn_ptr = POINTER(ngtcp2_conn)()
            # ngtcp2 expects dcid = client's SCID (from Initial packet) so that when client sends initial_scid it matches conn->dcid.current.cid
            dcid_cid = ngtcp2_cid(self.client_scid)
            scid_cid = ngtcp2_cid(self.scid)
            
            # Debug: log path before server_new
            logger.info(
                f"Calling server_new with path: "
                f"local.addr=0x{self.path_storage.path.local.addr:x}, "
                f"local.addrlen={self.path_storage.path.local.addrlen}, "
                f"remote.addr=0x{self.path_storage.path.remote.addr:x}, "
                f"remote.addrlen={self.path_storage.path.remote.addrlen}"
            )
            
            # Create server connection
            # Note: ngtcp2_conn_server_new may be a wrapper function
            result = ngtcp2_conn_server_new(
                byref(conn_ptr),  # conn (out)
                byref(dcid_cid),  # dcid = client's Initial SCID (for initial_scid validation)
                byref(scid_cid),  # scid = our new SCID
                byref(self.path_storage.path),  # path (must be non-NULL)
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
            
            if not conn_ptr:
                logger.error("ngtcp2_conn_server_new returned NULL")
                return False
            
            # Keep both the pointer and its contents
            self._conn_ptr = conn_ptr  # Keep pointer reference
            self.conn = conn_ptr.contents
            
            # Get the actual pointer value (address) for crypto callbacks
            conn_ptr_value = ctypes.cast(conn_ptr, c_void_p).value
            
            # Create and associate TLS session with the connection
            self.tls_session = create_server_tls_session()

            # Enable QUIC keep-alive packets to prevent idle timeout (30s server idle).
            # Send keep-alive after 10s of inactivity.
            if ngtcp2_conn_set_keep_alive_timeout:
                ngtcp2_conn_set_keep_alive_timeout(self._conn_ptr, 10 * NGTCP2_SECONDS)
            if not self.tls_session:
                logger.error("Failed to create TLS session for connection")
                return False
            
            # Set up crypto conn_ref so ngtcp2_crypto callbacks can find the conn
            # The get_conn callback returns the ngtcp2_conn from the conn_ref
            
            # Create get_conn callback: ngtcp2_conn* get_conn(ngtcp2_crypto_conn_ref* ref)
            @CFUNCTYPE(c_void_p, c_void_p)
            def get_conn_callback(ref_ptr):
                # ref_ptr points to our crypto_conn_ref, user_data contains conn ptr
                if ref_ptr:
                    ref = ctypes.cast(ref_ptr, POINTER(ngtcp2_crypto_conn_ref)).contents
                    return ref.user_data
                return None
            
            # Keep reference to prevent garbage collection
            self._get_conn_callback = get_conn_callback
            
            # Create the conn_ref structure
            self.crypto_conn_ref = ngtcp2_crypto_conn_ref()
            self.crypto_conn_ref.user_data = conn_ptr_value
            self.crypto_conn_ref.get_conn = ctypes.cast(get_conn_callback, c_void_p).value
            
            # Store conn_ref pointer in TLS session app data so ngtcp2 crypto add_handshake_data can get_conn.
            # ngtcp2 requires wolfSSL_set_app_data (not ex_data); see ngtcp2_crypto_wolfssl.h and tls_server_session_wolfssl.cc.
            conn_ref_ptr = ctypes.cast(ctypes.pointer(self.crypto_conn_ref), c_void_p).value
            if getattr(_tls_mod, 'USE_WOLFSSL', False):
                _tls_mod._ensure_wolfssl_bound()
                set_app = getattr(_tls_mod, 'wolfSSL_set_app_data', None)
                set_ex = getattr(_tls_mod, 'wolfSSL_set_ex_data', None)
                if self.tls_session:
                    if set_app:
                        set_app(self.tls_session, conn_ref_ptr)
                        logger.info("Set wolfSSL session app_data to crypto_conn_ref (required for handshake)")
                    if set_ex:
                        set_ex(self.tls_session, 0, conn_ref_ptr)
                    if not set_app:
                        logger.warning(
                            "wolfSSL_set_app_data not found in libwolfssl (ngtcp2 needs it for add_handshake_data). "
                            "Rebuild WolfSSL with --enable-opensslall."
                        )
                    if not set_app and not set_ex:
                        logger.warning("wolfSSL_set_app_data/set_ex_data not available - crypto callbacks may fail")
            else:
                _ensure_openssl_bound()
                ssl_for_app_data = self.tls_session
                if getattr(_tls_mod, 'ngtcp2_crypto_ossl_ctx_get_ssl', None):
                    ssl_for_app_data = _tls_mod.ngtcp2_crypto_ossl_ctx_get_ssl(self.tls_session)
                if _tls_mod.SSL_set_app_data and ssl_for_app_data:
                    _tls_mod.SSL_set_app_data(ssl_for_app_data, conn_ref_ptr)
                    logger.debug("Set TLS session app data to crypto_conn_ref")
                else:
                    logger.warning("SSL_set_app_data not available - crypto callbacks may fail")
            
            # Associate TLS native handle with ngtcp2 connection (crypto_ossl_ctx*, not raw SSL*)
            if ngtcp2_conn_set_tls_native_handle:
                ngtcp2_conn_set_tls_native_handle(self._conn_ptr, self.tls_session)
                logger.debug("Associated TLS session with ngtcp2 connection")
            else:
                logger.warning("ngtcp2_conn_set_tls_native_handle not available")
            
            self.state = "handshake"
            logger.info(f"Created ngtcp2 connection: dcid={self.dcid.hex()[:8]}, scid={self.scid.hex()[:8]}")
            
            # CRITICAL FIX: For local networks, force path validation to bypass amplification limit
            if ngtcp2_conn_force_validate_path and ngtcp2_path_is_local_network:
                if ngtcp2_path_is_local_network(byref(self.path_storage.path)):
                    result = ngtcp2_conn_force_validate_path(self._conn_ptr)
                    if result == 0:
                        logger.info("Forced path validation for local network (amplification limit bypassed)")
                    else:
                        logger.warning("Failed to force path validation")
            
            return True
            
        except Exception as e:
            logger.error(f"Error initializing ngtcp2 connection: {e}", exc_info=True)
            return False
    
    def _fill_path_storage(self):
        """Fill path_storage with local (server) and remote (client) addresses.
        Uses ngtcp2_path_storage_init API to properly initialize the structure.
        """
        try:
            # IPv4 sockaddr_in: family=2, port=network order, addr=network order, zero padding
            def fill_sockaddr_in(sa: ngtcp2_sockaddr_in, host: str, port: int) -> None:
                sa.sin_family = socket.AF_INET
                sa.sin_port = socket.htons(port)
                # sin_addr.s_addr is 4 bytes in network byte order (big-endian)
                addr_bytes = socket.inet_pton(socket.AF_INET, host)
                for i in range(4):
                    sa.sin_addr.s_addr[i] = addr_bytes[i]
                # sin_zero already zeroed by ctypes
            
            # Use the exact IP we're binding to (server.host)
            # CRITICAL: Server MUST bind to actual IP (not 0.0.0.0) for ngtcp2 path validation
            local_host = self.server.host
            
            # Create sockaddr structures as instance variables (prevents garbage collection)
            self._local_sockaddr = ngtcp2_sockaddr_in()
            self._remote_sockaddr = ngtcp2_sockaddr_in()
            fill_sockaddr_in(self._local_sockaddr, local_host, self.server.port)
            fill_sockaddr_in(self._remote_sockaddr, self.remote_addr[0], self.remote_addr[1])
            
            addrlen = ctypes.sizeof(ngtcp2_sockaddr_in)
            
            # Manually copy sockaddr data into path_storage buffers (ctypes doesn't call ngtcp2_path_storage_init correctly)
            # Copy local sockaddr
            ctypes.memmove(
                ctypes.addressof(self.path_storage.local_addrbuf),
                ctypes.addressof(self._local_sockaddr),
                addrlen
            )
            # Copy remote sockaddr
            ctypes.memmove(
                ctypes.addressof(self.path_storage.remote_addrbuf),
                ctypes.addressof(self._remote_sockaddr),
                addrlen
            )
            
            # Set path pointers to the buffers
            self.path_storage.path.local.addr = ctypes.addressof(self.path_storage.local_addrbuf)
            self.path_storage.path.local.addrlen = addrlen
            self.path_storage.path.remote.addr = ctypes.addressof(self.path_storage.remote_addrbuf)
            self.path_storage.path.remote.addrlen = addrlen
            self.path_storage.path.user_data = None
            
            # Debug: verify the copy worked
            # local_buf_hex = ctypes.string_at(ctypes.addressof(self.path_storage.local_addrbuf), 16).hex()
            # remote_buf_hex = ctypes.string_at(ctypes.addressof(self.path_storage.remote_addrbuf), 16).hex()
            # logger.info(
            #     f"Path storage manually initialized: "
            #     f"local={local_host}:{self.server.port} (hex={local_buf_hex}), "
            #     f"remote={self.remote_addr[0]}:{self.remote_addr[1]} (hex={remote_buf_hex}), "
            #     f"local.addr=0x{self.path_storage.path.local.addr:x}, "
            #     f"remote.addr=0x{self.path_storage.path.remote.addr:x}"
            # )
        except Exception as e:
            logger.error(f"Failed to initialize path storage: {e}", exc_info=True)
            raise
    
    def _get_actual_local_ip(self) -> str:
        """Get the actual local IP that would be used to reach the client (not 0.0.0.0)."""
        try:
            # Create a socket to the remote to discover which local IP would be used
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect((self.remote_addr[0], 1))  # port doesn't matter
                local_ip = s.getsockname()[0]
                logger.info(f"Auto-detected local IP for path: {local_ip} (remote: {self.remote_addr[0]})")
                return local_ip
        except Exception as e:
            # Fallback: use server host or 127.0.0.1
            fallback = self.server.host if self.server.host != "0.0.0.0" else "127.0.0.1"
            logger.warning(f"Failed to detect local IP ({e}), using fallback: {fallback}")
            return fallback
    
    def _setup_callbacks(self):
        """Set up ngtcp2 connection callbacks using crypto library helpers"""
        # Ensure crypto callbacks are bound (handles circular import issues)
        _ensure_callbacks_bound()
        
        # Use ngtcp2_crypto callback implementations for crypto operations
        # These are provided by libngtcp2_crypto_ossl and handle TLS integration
        
        # Server-required callbacks from ngtcp2_crypto
        if _tls_mod.ngtcp2_crypto_recv_client_initial_cb:
            self.callbacks.recv_client_initial = _tls_mod.ngtcp2_crypto_recv_client_initial_cb
        if _tls_mod.ngtcp2_crypto_recv_crypto_data_cb:
            self.callbacks.recv_crypto_data = _tls_mod.ngtcp2_crypto_recv_crypto_data_cb
        if _tls_mod.ngtcp2_crypto_encrypt_cb:
            self.callbacks.encrypt = _tls_mod.ngtcp2_crypto_encrypt_cb
        if _tls_mod.ngtcp2_crypto_decrypt_cb:
            self.callbacks.decrypt = _tls_mod.ngtcp2_crypto_decrypt_cb
        if _tls_mod.ngtcp2_crypto_hp_mask_cb:
            self.callbacks.hp_mask = _tls_mod.ngtcp2_crypto_hp_mask_cb
        if _tls_mod.ngtcp2_crypto_update_key_cb:
            self.callbacks.update_key = _tls_mod.ngtcp2_crypto_update_key_cb
        if _tls_mod.ngtcp2_crypto_delete_crypto_aead_ctx_cb:
            self.callbacks.delete_crypto_aead_ctx = _tls_mod.ngtcp2_crypto_delete_crypto_aead_ctx_cb
        if _tls_mod.ngtcp2_crypto_delete_crypto_cipher_ctx_cb:
            self.callbacks.delete_crypto_cipher_ctx = _tls_mod.ngtcp2_crypto_delete_crypto_cipher_ctx_cb
        if _tls_mod.ngtcp2_crypto_get_path_challenge_data_cb:
            self.callbacks.get_path_challenge_data = _tls_mod.ngtcp2_crypto_get_path_challenge_data_cb

        # recv_stream_data callback: deliver application data to stream buffers
        @CFUNCTYPE(c_int, c_void_p, c_uint32, c_int64, c_uint64, POINTER(c_uint8), c_size_t, c_void_p, c_void_p)
        def recv_stream_data_callback(conn, flags, stream_id, offset, data, datalen, user_data, stream_user_data):
            try:
                conn_obj = _CONN_REGISTRY.get(int(user_data)) if user_data else None
                if not conn_obj:
                    logger.debug("recv_stream_data_callback: no conn for user_data=%s", user_data)
                    return 0
                stream = conn_obj.get_stream(stream_id)
                if datalen:
                    offset_val = int(offset)
                    end_offset = offset_val + int(datalen)
                    # Drop fully duplicated data (retransmissions)
                    if end_offset <= stream.rx_offset:
                        if flags & NGTCP2_STREAM_DATA_FLAG_FIN:
                            stream.state = "closed"
                        return 0
                    payload = ctypes.string_at(data, datalen)
                    if offset_val < stream.rx_offset:
                        skip = stream.rx_offset - offset_val
                        payload = payload[skip:]
                    if payload:
                        stream.append_data(payload, fin=bool(flags & NGTCP2_STREAM_DATA_FLAG_FIN))
                        if not getattr(conn_obj, "_logged_first_stream", False):
                            conn_obj._logged_first_stream = True
                        # Update flow control windows only for new bytes
                        if ngtcp2_conn_extend_max_stream_offset:
                            ngtcp2_conn_extend_max_stream_offset(conn_obj._conn_ptr, stream_id, len(payload))
                        if ngtcp2_conn_extend_max_offset:
                            ngtcp2_conn_extend_max_offset(conn_obj._conn_ptr, len(payload))
            except Exception as e:
                logger.debug("recv_stream_data_callback error: %s", e)
            return 0

        # Keep reference to prevent garbage collection
        self._recv_stream_data_callback = recv_stream_data_callback
        self.callbacks.recv_stream_data = ctypes.cast(recv_stream_data_callback, c_void_p).value
        
        # rand callback - use Python's os.urandom via ctypes wrapper
        # Create a CFUNCTYPE for the rand callback signature:
        # void rand(uint8_t *dest, size_t destlen, const ngtcp2_rand_ctx *rand_ctx)
        @CFUNCTYPE(None, POINTER(c_uint8), c_size_t, c_void_p)
        def rand_callback(dest, destlen, rand_ctx):
            import os
            random_bytes = os.urandom(destlen)
            for i in range(destlen):
                dest[i] = random_bytes[i]
        
        # Keep reference to prevent garbage collection
        self._rand_callback = rand_callback
        self.callbacks.rand = ctypes.cast(rand_callback, c_void_p).value
        
        # get_new_connection_id callback
        # int get_new_connection_id(ngtcp2_conn *conn, ngtcp2_cid *cid, uint8_t *token, size_t cidlen, void *user_data)
        @CFUNCTYPE(c_int, c_void_p, POINTER(ngtcp2_cid), POINTER(c_uint8), c_size_t, c_void_p)
        def get_new_connection_id_callback(conn, cid, token, cidlen, user_data):
            import os
            # Generate random connection ID
            random_cid = os.urandom(cidlen)
            cid.contents.datalen = cidlen
            for i in range(cidlen):
                cid.contents.data[i] = random_cid[i]
            # Generate random stateless reset token (16 bytes)
            random_token = os.urandom(16)
            for i in range(16):
                token[i] = random_token[i]
            # Register new CID so packets using it map to this connection
            try:
                if user_data:
                    conn_obj = _CONN_REGISTRY.get(int(user_data))
                    if conn_obj:
                        cid_bytes = bytes(cid.contents.data[:cidlen])
                        conn_obj.cids.add(cid_bytes)
                        conn_obj.server.connections[cid_bytes] = conn_obj
            except Exception:
                pass
            return 0  # Success
        
        # Keep reference to prevent garbage collection
        self._get_new_connection_id_callback = get_new_connection_id_callback
        self.callbacks.get_new_connection_id = ctypes.cast(get_new_connection_id_callback, c_void_p).value
        
        logger.debug("ngtcp2 callbacks configured")
    
    def recv_packet(self, data: bytes, timestamp: Optional[int] = None, addr: Optional[Tuple[str, int]] = None) -> bool:
        """
        Receive and process a QUIC packet
        
        Based on curl's cf_ngtcp2_recv_pkts and cf_progress_ingress
        """
        if not self.conn:
            return False
        
        try:
            if timestamp is None:
                timestamp = time.monotonic_ns()
            # ngtcp2 requires ts >= conn->log.last_ts (monotonic); ensure >= initial_ts and last used
            timestamp = max(timestamp, self.settings.initial_ts)
            timestamp = max(timestamp, getattr(self, '_last_ts', 0))
            self._last_ts = timestamp

            # Always pass the same path we used for server_new
            path_ptr = byref(self.path_storage.path)
            
            # Debug: Compare our path with ngtcp2's internal path
            # if ngtcp2_conn_get_path:
            #     internal_path = ngtcp2_conn_get_path(self._conn_ptr)
            #     if internal_path:
            #         # Log both paths for comparison
            #         our_local = ctypes.string_at(self.path_storage.path.local.addr, self.path_storage.path.local.addrlen).hex()
            #         our_remote = ctypes.string_at(self.path_storage.path.remote.addr, self.path_storage.path.remote.addrlen).hex()
            #         ngtcp2_local = ctypes.string_at(internal_path.contents.local.addr, internal_path.contents.local.addrlen).hex()
            #         ngtcp2_remote = ctypes.string_at(internal_path.contents.remote.addr, internal_path.contents.remote.addrlen).hex()
            #         
            #         paths_match = (our_local == ngtcp2_local and our_remote == ngtcp2_remote)
            #         logger.info(
            #             f"Path comparison: match={paths_match}\n"
            #             f"  Our path:     local={our_local} remote={our_remote}\n"
            #             f"  ngtcp2 path:  local={ngtcp2_local} remote={ngtcp2_remote}"
            #         )

            # Convert data to ctypes
            pkt_data = (c_uint8 * len(data)).from_buffer_copy(data)

            with self._ts_lock:
                # Read packet into connection (versioned API: 7 args; path must be non-NULL)
                result = ngtcp2_conn_read_pkt(
                    self._conn_ptr,
                    path_ptr,
                    NGTCP2_PKT_INFO_V1,  # required for versioned API (0 can trigger unreachable)
                    None,  # pkt_info (can be NULL)
                    pkt_data,
                    len(data),
                    timestamp,
                )
                # ngtcp2 may set conn->log.last_ts > ts even when read_pkt returns error; ensure
                # next write_pkt gets ts strictly greater so conn_update_timestamp assertion holds
                self._last_ts = max(self._last_ts, timestamp + 1_000_000)  # advance 1ms
                # Sync with ngtcp2's conn->log.last_ts so we never pass ts < last_ts to write_pkt
                if ngtcp2_conn_get_timestamp and self._conn_ptr:
                    self._last_ts = max(self._last_ts, ngtcp2_conn_get_timestamp(self._conn_ptr))

            # logger.info(f"ngtcp2_conn_read_pkt returned {result} for {len(data)} bytes from {addr}")
            
            # Debug: Check TLS alert if crypto error
            if result != 0 and ngtcp2_conn_get_tls_alert:
                try:
                    alert = ngtcp2_conn_get_tls_alert(self._conn_ptr)
                    if alert != 0:
                        logger.warning(f"TLS alert: {alert}")
                except:
                    pass

            if result != 0:
                err_msg = _ngtcp2_read_pkt_error_message(result)
                logger.warning(f"ngtcp2_conn_read_pkt returned error: {result} ({err_msg})")
                if result in (
                    -225,  # NGTCP2_ERR_TRANSPORT_PARAM
                    -215,  # NGTCP2_ERR_REQUIRED_TRANSPORT_PARAM
                    -216,  # NGTCP2_ERR_MALFORMED_TRANSPORT_PARAM
                ):
                    logger.info(
                        "QUIC transport parameter error: client params failed validation. "
                        "Ensure client sends valid params (e.g. active_connection_id_limit>=2, "
                        "max_ack_delay in range). Client and server ngtcp2 versions should match."
                    )
                    # Debug: log decoded client transport params if available (may be NULL on -225)
                    if result == -225 and ngtcp2_conn_get_remote_transport_params:
                        try:
                            rtp = ngtcp2_conn_get_remote_transport_params(self._conn_ptr)
                            if rtp:
                                p = rtp.contents
                                logger.info(
                                    "Decoded client transport params: active_connection_id_limit=%s, "
                                    "max_ack_delay=%s ns (valid: active_connection_id_limit>=%s, "
                                    "max_ack_delay < 16384 ms)",
                                    getattr(p, "active_connection_id_limit", "?"),
                                    getattr(p, "max_ack_delay", "?"),
                                    NGTCP2_DEFAULT_ACTIVE_CONNECTION_ID_LIMIT,
                                )
                            else:
                                logger.info("Decoded client transport params: (NULL - not set before validation failure)")
                        except Exception as e:
                            logger.debug("Could not get remote transport params: %s", e)
                return False

            self.packets_received += 1
            self.bytes_received += len(data)
            self.last_packet_at = time.time()
            self.last_io_at = time.time()
            
            # Check if handshake completed
            if ngtcp2_conn_get_handshake_completed:
                if ngtcp2_conn_get_handshake_completed(self._conn_ptr):
                    if not self.handshake_completed:
                        self.handshake_completed = True
                        self.state = "connected"
                        logger.info(f"Handshake completed for connection {self.dcid.hex()[:8]}")
                        
                        # Override peer idle timeout if it is too low; ngtcp2 uses the
                        # smaller of local/remote. Setting remote to 0 makes local apply.
                        if ngtcp2_conn_get_remote_transport_params and not self._remote_idle_overridden:
                            try:
                                rtp = ngtcp2_conn_get_remote_transport_params(self._conn_ptr)
                                if rtp:
                                    rtp.contents.max_idle_timeout = 0
                                    self._remote_idle_overridden = True
                                    logger.info("Overrode remote max_idle_timeout to 0 (use local idle timeout)")
                            except Exception as e:
                                logger.debug("Failed to override remote max_idle_timeout: %s", e)
                        
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
            # Push any queued stream data into ngtcp2 before writing packets
            for stream_id, stream in list(self.streams.items()):
                if not stream.send_buffer:
                    continue
                data = bytes(stream.send_buffer)
                try:
                    if ngtcp2_conn_writev_stream_versioned:
                        vec = ngtcp2_vec()
                        data_array = (c_uint8 * len(data)).from_buffer_copy(data)
                        vec.base = cast(data_array, POINTER(c_uint8))
                        vec.len = len(data)
                        with self._ts_lock:
                            ts = time.monotonic_ns()
                            if ngtcp2_conn_get_timestamp and self._conn_ptr:
                                ts = max(ts, ngtcp2_conn_get_timestamp(self._conn_ptr) + 1)
                            ts = max(ts, getattr(self, "_last_ts", 0) + 1)
                            self._last_ts = ts
                            pkt_buf = (c_uint8 * MAX_UDP_PAYLOAD_SIZE)()
                            pdatalen = c_ssize_t(0)
                            rv = ngtcp2_conn_writev_stream_versioned(
                                self._conn_ptr,
                                byref(self.path_storage.path),
                                NGTCP2_PKT_INFO_V1,
                                None,  # pkt_info
                                pkt_buf,
                                MAX_UDP_PAYLOAD_SIZE,
                                byref(pdatalen),
                                0,  # flags
                                stream_id,
                                byref(vec),
                                1,  # datavcnt
                                c_uint64(ts),
                            )
                        if rv > 0:
                            pkt_len = int(rv)
                            consumed = int(pdatalen.value)
                            if consumed > 0:
                                if consumed >= len(stream.send_buffer):
                                    stream.send_buffer.clear()
                                else:
                                    del stream.send_buffer[:consumed]
                            pkt_data = bytes(pkt_buf[:pkt_len])
                            self.server.send_packet(pkt_data, self.remote_addr)
                            self.packets_sent += 1
                            self.bytes_sent += pkt_len
                            self.last_io_at = time.time()
                        else:
                            logger.debug("ngtcp2_conn_writev_stream_versioned failed: %s", rv)
                    elif ngtcp2_conn_submit_stream_data:
                        buf = (c_uint8 * len(data)).from_buffer_copy(data)
                        rv = ngtcp2_conn_submit_stream_data(self._conn_ptr, 0, stream_id, buf, len(data))
                        if rv == 0:
                            stream.send_buffer.clear()
                        else:
                            logger.debug("ngtcp2_conn_submit_stream_data failed: %s", rv)
                    elif ngtcp2_strm_write:
                        written = ngtcp2_strm_write(self._conn_ptr, stream_id, data, len(data), 0)
                        if written > 0:
                            del stream.send_buffer[:written]
                except Exception as e:
                    logger.debug("Stream submit/write failed: %s", e)

            # Always use a fresh monotonic timestamp so we never pass ts < conn->log.last_ts
            # (avoids assertion in conn_update_timestamp when e.g. maintenance and recv run concurrently)
            now_ns = time.monotonic_ns()
            timestamp = max(now_ns, getattr(self, '_last_ts', self.settings.initial_ts))
            if ngtcp2_conn_get_timestamp and self._conn_ptr:
                timestamp = max(timestamp, ngtcp2_conn_get_timestamp(self._conn_ptr))
            self._last_ts = timestamp

            # Write packets (ngtcp2 will call our send callback)
            # We need to call ngtcp2_conn_write_pkt in a loop until no more packets.
            # Hold _ts_lock so get_timestamp + write_pkt are not interleaved with recv_packet's read_pkt.
            max_packets = MAX_PKT_BURST
            packets_sent = 0

            with self._ts_lock:
                while packets_sent < max_packets:
                    # Pass ts strictly > conn->log.last_ts so conn_update_timestamp assertion never fails.
                    # When available, use library's last_ts + 1 (only source of truth). Else use fresh monotonic.
                    if ngtcp2_conn_get_timestamp and self._conn_ptr:
                        ts_to_pass = ngtcp2_conn_get_timestamp(self._conn_ptr) + 1
                    else:
                        now = time.monotonic_ns()
                        ts_to_pass = max(now, timestamp, self._last_ts + 1)
                    self._last_ts = ts_to_pass
                    timestamp = ts_to_pass

                    # Allocate buffer for packet
                    pkt_buf = (c_uint8 * MAX_UDP_PAYLOAD_SIZE)()
                    pktlen = c_size_t(0)

                    # Write packet (unified 6-arg API: conn, path, dest, destlen, pdatalen, ts)
                    ts_c = c_uint64(ts_to_pass & 0xFFFFFFFFFFFFFFFF)
                    result = ngtcp2_conn_write_pkt(
                        self._conn_ptr,
                        byref(self.path_storage.path),
                        pkt_buf,
                        MAX_UDP_PAYLOAD_SIZE,
                        byref(pktlen),
                        ts_c,
                    )

                    if result < 0:
                        # Error (negative ngtcp2_ssize)
                        break

                    # ngtcp2_conn_write_pkt returns bytes written (ngtcp2_ssize). Some ctypes
                    # wrappers do not reliably set pdatalen via byref; fall back to result.
                    pkt_len = pktlen.value
                    if pkt_len == 0 and result > 0:
                        pkt_len = int(result)

                    if pkt_len > 0:
                        # Send packet via UDP
                        pkt_data = bytes(pkt_buf[:pkt_len])
                        self.server.send_packet(pkt_data, self.remote_addr)
                        packets_sent += 1
                        self.packets_sent += 1
                        self.bytes_sent += pkt_len
                        self.last_io_at = time.time()
                        # Log first outgoing packet so we can confirm server is responding to client
                        # if packets_sent == 1 and not getattr(self, "_logged_first_send", False):
                        #     self._logged_first_send = True
                        #     logger.info(
                        #         "Sent first QUIC response (len=%d) to %s:%d",
                        #         len(pkt_data),
                        #         self.remote_addr[0],
                        #         self.remote_addr[1],
                        #     )
                        # Sync _last_ts with ngtcp2 so next write_pkt (same loop) gets ts >= conn->log.last_ts
                        if ngtcp2_conn_get_timestamp and self._conn_ptr:
                            self._last_ts = max(self._last_ts, ngtcp2_conn_get_timestamp(self._conn_ptr))
                            timestamp = self._last_ts
                    else:
                        # ngtcp2 had nothing to send (e.g. no TLS data yet - add_handshake_data not called)
                        if packets_sent == 0:
                            # Throttle: log once per connection, then at most every 5 seconds (avoids log spam in server.log)
                            now_sec = time.time()
                            last_log = getattr(self, "_logged_write_pkt_zero_at", None)
                            if last_log is None or (now_sec - last_log) >= 5.0:
                                self._logged_write_pkt_zero_at = now_sec
                                # logger.info(
                                #     "ngtcp2_conn_write_pkt returned 0 bytes (no packet to send). "
                                #     "Possible causes: (1) WolfSSL not producing ServerHello (check WOLFSSL: add handshake data logs), "
                                #     "(2) amplification limit (bytes_recv=0 so server_tx_left=0), "
                                #     "(3) pacing/congestion control. "
                                #     "Debug: pkts_received=%d, bytes_received=%d. "
                                #     "If handshake keeps timing out: rebuild ngtcp2 with PRINTF_DEBUG 1 in "
                                #     "crypto/wolfssl/wolfssl.c to see if recv_crypto_data/do_handshake run (stderr).",
                                #     self.packets_received,
                                #     self.bytes_received,
                                # )
                        break

            return True
            
        except Exception as e:
            logger.error(f"Error sending packets: {e}", exc_info=True)
            return False
    
    def handle_expiry(self, timestamp: Optional[int] = None) -> bool:
        """
        Handle connection expiry/timeouts
        
        Based on curl's check_and_set_expiry. ngtcp2 requires ts > conn->log.last_ts.
        """
        if not self.conn or self.state == "closed":
            return False
        
        try:
            if timestamp is None:
                timestamp = time.monotonic_ns()
            
            # Get expiry time
            if not ngtcp2_conn_get_expiry:
                return True
            expiry = ngtcp2_conn_get_expiry(self._conn_ptr)
            if expiry == 0xFFFFFFFFFFFFFFFF:  # UINT64_MAX
                return True
            if expiry > timestamp:
                return True

            # Clamp timestamp so ts > conn->log.last_ts (ngtcp2 assertion)
            with self._ts_lock:
                if ngtcp2_conn_get_timestamp and self._conn_ptr:
                    ts_to_pass = max(
                        timestamp,
                        ngtcp2_conn_get_timestamp(self._conn_ptr) + 1,
                        getattr(self, "_last_ts", 0) + 1,
                    )
                else:
                    ts_to_pass = max(timestamp, getattr(self, "_last_ts", 0) + 1)
                ts_to_pass = c_uint64(ts_to_pass).value
                self._last_ts = ts_to_pass

                result = ngtcp2_conn_handle_expiry(self._conn_ptr, c_uint64(ts_to_pass))
                if result != 0:
                    # Expected timeout/close: mark closed and log once (avoid continuous log)
                    if result == NGTCP2_ERR_HANDSHAKE_TIMEOUT or result == NGTCP2_ERR_IDLE_CLOSE:
                        if self.state != "closed":
                            logger.info(
                                "Connection closed: %s",
                                "handshake timeout" if result == NGTCP2_ERR_HANDSHAKE_TIMEOUT else "idle timeout",
                            )
                        self.state = "closed"
                        return False
                    logger.warning(f"ngtcp2_conn_handle_expiry returned error: {result}")
                    return False

            # Try to send packets after handling expiry (uses its own ts clamping)
            self.send_packets(ts_to_pass)
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
            # ngtcp2 asserts conn->log.last_ts <= ts in conn_update_timestamp; use monotonic ts >= last_ts to avoid crash on shutdown
            now_ns = time.monotonic_ns()
            with self._ts_lock:
                last_ts = getattr(self, "_last_ts", now_ns)
                timestamp = max(now_ns, last_ts + 1)
                if ngtcp2_conn_get_timestamp and self._conn_ptr:
                    timestamp = max(timestamp, ngtcp2_conn_get_timestamp(self._conn_ptr) + 1)
                self._last_ts = timestamp
            if ngtcp2_conn_close:
                # ngtcp2_conn_close is a wrapper that handles packet sending
                result = ngtcp2_conn_close(
                    self._conn_ptr,
                    byref(self.path_storage.path),
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
        
        # Delete ngtcp2 connection (must pass actual conn pointer)
        if self._conn_ptr and ngtcp2_conn_del:
            try:
                ngtcp2_conn_del(self._conn_ptr, None)  # mem = NULL
            except Exception as e:
                logger.warning(f"Error deleting ngtcp2 connection: {e}")
        self._conn_ptr = None
        self.conn = None
        _CONN_REGISTRY.pop(id(self), None)
        
        # Free TLS session
        if self.tls_session:
            free_tls_session(self.tls_session)
            self.tls_session = None
        
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
        
        # Create server TLS context with certificate and key (OpenSSL 3.x = TLS 1.3)
        if self.certfile and self.keyfile:
            tls_ctx = create_server_tls_ctx(self.certfile, self.keyfile)
            if not tls_ctx:
                logger.error("Failed to create server TLS context - QUIC handshakes will fail")
            else:
                logger.info("Created server TLS context for QUIC")
        else:
            logger.warning("No certificate/key files provided - QUIC handshakes will fail")
        
        # Create UDP socket
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 2 * 1024 * 1024)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 2 * 1024 * 1024)
        # Enable IP_PKTINFO to get actual local address for each packet (required for ngtcp2 path validation and bytes_recv)
        self.sock.setsockopt(socket.IPPROTO_IP, socket.IP_PKTINFO, 1)
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
                timestamp = time.monotonic_ns()
                
                # Handle expiry for all connections
                for conn in list(self.connections.values()):
                    conn.handle_expiry(timestamp)
                    conn.send_packets(timestamp)
                
                # Clean up closed connections (handle multiple CIDs per connection)
                to_remove = []
                closed_conns = set()
                for dcid, conn in self.connections.items():
                    if conn.state == "closed":
                        to_remove.append(dcid)
                        closed_conns.add(conn)
                
                for dcid in to_remove:
                    self.connections.pop(dcid, None)
                
                for conn in closed_conns:
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
                        cids = self._extract_initial_cids(data)
                        if not cids:
                            logger.debug("Could not extract DCID/SCID from Initial packet")
                            return
                        pkt_dcid, pkt_scid = cids
                        scid = secrets.token_bytes(8)  # Generate server CID
                        conn = NGTCP2Connection(self, pkt_dcid, pkt_scid, scid, addr)
                        if conn.initialize():
                            # Map both the client's Initial DCID and our server SCID to this connection.
                            # After receiving the server Initial, the client will switch DCID to our SCID.
                            conn.cids.update({pkt_dcid, scid})
                            self.connections[pkt_dcid] = conn
                            self.connections[scid] = conn
                            logger.info(f"Accepted new connection from {addr}")
                        else:
                            logger.error("Failed to initialize new connection")
                            return
                    else:
                        logger.warning("ngtcp2_accept not available")
                        return
                else:
                    # Unknown connection, drop packet
                    logger.info(f"Dropping packet for unknown connection: {dcid.hex()[:8]} from {addr}")
                    return
            
            # Process packet in connection
            timestamp = time.monotonic_ns()
            if conn.recv_packet(data, timestamp, addr):
                # Try to send any pending packets (Initial/Handshake response)
                conn.send_packets(timestamp)
                if not getattr(conn, "_logged_first_recv", False):
                    conn._logged_first_recv = True
                    # Retry send after short delays in case crypto produces data asynchronously
                    if conn.state == "handshake":
                        try:
                            loop = asyncio.get_running_loop()
                            def _delayed_send(c, key):
                                if c.state != "closed" and key in self.connections:
                                    c.send_packets(time.monotonic_ns())
                            for delay in (0.03, 0.08):
                                loop.call_later(delay, _delayed_send, conn, dcid)
                        except RuntimeError:
                            pass
            else:
                logger.warning(f"Failed to process packet for connection {dcid.hex()[:8]}")
                # Remove connection so maintenance never calls send_packets (avoids ngtcp2
                # conn_update_timestamp assertion when conn->log.last_ts > ts after read_pkt error)
                # Remove all CID mappings for this connection.
                if conn and getattr(conn, "cids", None):
                    for cid in list(conn.cids):
                        self.connections.pop(cid, None)
                else:
                    self.connections.pop(dcid, None)
                conn.cleanup()

        except Exception as e:
            logger.error(f"Error handling packet: {e}", exc_info=True)
    
    def _extract_dcid(self, data: bytes) -> Optional[bytes]:
        """Extract Destination Connection ID from packet (for connection map key)."""
        # Long header (Initial/Handshake/0-RTT) -> use standard parsing
        if len(data) > 0 and (data[0] & 0x80) == 0x80:
            r = self._extract_initial_cids(data)
            return r[0] if r else None
        # Short header (1-RTT): DCID length is implicit; match against known CIDs
        if len(data) < 2 or not self.connections:
            return None
        # Try all known CID lengths (longer first to avoid prefix collisions)
        lengths = sorted({len(cid) for cid in self.connections.keys() if cid}, reverse=True)
        for cid_len in lengths:
            if len(data) >= 1 + cid_len:
                cand = data[1:1 + cid_len]
                if cand in self.connections:
                    return cand
        return None

    def _extract_initial_cids(self, data: bytes) -> Optional[Tuple[bytes, bytes]]:
        """Extract (DCID, SCID) from QUIC long header (Initial/Handshake/0-RTT). QUIC long header: flags(1), version(4), DCID len(1), DCID, SCID len(1), SCID, ..."""
        if len(data) < 8:
            return None
        try:
            if (data[0] & 0x80) != 0x80:  # Long header (any type)
                return None
            off = 5  # after version
            dcid_len = data[off]
            off += 1
            if dcid_len > NGTCP2_MAX_CIDLEN or len(data) < off + dcid_len + 1:
                return None
            dcid = data[off : off + dcid_len]
            off += dcid_len
            scid_len = data[off]
            off += 1
            if scid_len > NGTCP2_MAX_CIDLEN or len(data) < off + scid_len:
                return None
            scid = data[off : off + scid_len]
            return (dcid, scid)
        except Exception:
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
