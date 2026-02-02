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
        ngtcp2_settings_default, ngtcp2_transport_params_default,
        ngtcp2_conn_server_new, ngtcp2_accept, ngtcp2_conn_read_pkt,
        ngtcp2_conn_write_pkt, ngtcp2_conn_handle_expiry, ngtcp2_conn_close,
        ngtcp2_conn_get_expiry, ngtcp2_conn_get_handshake_completed, ngtcp2_conn_del,
        ngtcp2_conn_get_remote_transport_params,
        ngtcp2_conn_extend_max_stream_offset, ngtcp2_conn_extend_max_offset,
        ngtcp2_conn_shutdown_stream, ngtcp2_conn_set_stream_user_data,
        ngtcp2_conn_get_stream_user_data,
        ngtcp2_strm_recv, ngtcp2_strm_write,
        NGTCP2_MILLISECONDS, NGTCP2_SECONDS, NGTCP2_MICROSECONDS,
        NGTCP2_MAX_CIDLEN, NGTCP2_PROTO_VER_V1, NGTCP2_MAX_UDP_PAYLOAD_SIZE,
        NGTCP2_DEFAULT_MAX_RECV_UDP_PAYLOAD_SIZE,
        NGTCP2_DEFAULT_ACK_DELAY_EXPONENT, NGTCP2_DEFAULT_MAX_ACK_DELAY,
        NGTCP2_DEFAULT_ACTIVE_CONNECTION_ID_LIMIT,
    )
except ImportError:
    from mqttd.ngtcp2_bindings import (
        NGTCP2_AVAILABLE, get_ngtcp2_lib,
        ngtcp2_cid, ngtcp2_conn, ngtcp2_settings, ngtcp2_transport_params,
        ngtcp2_path, ngtcp2_path_storage, ngtcp2_addr, ngtcp2_sockaddr_in,
        ngtcp2_conn_callbacks, ngtcp2_crypto_conn_ref, SendPacketFunc, RecvPacketFunc,
        ngtcp2_settings_default, ngtcp2_transport_params_default,
        ngtcp2_conn_server_new, ngtcp2_accept, ngtcp2_conn_read_pkt,
        ngtcp2_conn_write_pkt, ngtcp2_conn_handle_expiry, ngtcp2_conn_close,
        ngtcp2_conn_get_expiry, ngtcp2_conn_get_handshake_completed, ngtcp2_conn_del,
        ngtcp2_conn_get_remote_transport_params,
        ngtcp2_conn_extend_max_stream_offset, ngtcp2_conn_extend_max_offset,
        ngtcp2_conn_shutdown_stream, ngtcp2_conn_set_stream_user_data,
        ngtcp2_conn_get_stream_user_data,
        ngtcp2_strm_recv, ngtcp2_strm_write,
        NGTCP2_MILLISECONDS, NGTCP2_SECONDS, NGTCP2_MICROSECONDS,
        NGTCP2_MAX_CIDLEN, NGTCP2_PROTO_VER_V1, NGTCP2_MAX_UDP_PAYLOAD_SIZE,
        NGTCP2_DEFAULT_MAX_RECV_UDP_PAYLOAD_SIZE,
        NGTCP2_DEFAULT_ACK_DELAY_EXPONENT, NGTCP2_DEFAULT_MAX_ACK_DELAY,
        NGTCP2_DEFAULT_ACTIVE_CONNECTION_ID_LIMIT,
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

# Constants
MAX_PKT_BURST = 10
MAX_UDP_PAYLOAD_SIZE = 1452
QUIC_MAX_STREAMS = 256 * 1024
HANDSHAKE_TIMEOUT = 10 * NGTCP2_SECONDS  # 10 seconds

# ngtcp2 read_pkt error code -> human-readable message (from ngtcp2.h)
_NGTCP2_READ_PKT_ERRORS = {
    -225: "NGTCP2_ERR_TRANSPORT_PARAM (transport parameter error)",
    -216: "NGTCP2_ERR_MALFORMED_TRANSPORT_PARAM (malformed transport parameter)",
    -215: "NGTCP2_ERR_REQUIRED_TRANSPORT_PARAM (required transport parameter missing)",
}


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
        self._conn_ptr = None  # Keep the ctypes pointer reference
        
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
            self.settings.max_tx_udp_payload_size = max(1452, NGTCP2_MAX_UDP_PAYLOAD_SIZE)  # ngtcp2_conn.c assertion
            self.settings.handshake_timeout = HANDSHAKE_TIMEOUT
            self.settings.max_window = 100 * 1024 * 1024  # 100 MB
            self.settings.max_stream_window = 10 * 1024 * 1024  # 10 MB
            
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
            self.transport_params.max_idle_timeout = 0  # No idle timeout (valid; must not be UINT64_MAX)
            # active_connection_id_limit in [2, 7]; keep 2 from default
            self.transport_params.active_connection_id_limit = 2
            # Ensure ngtcp2-valid defaults for params checked during handshake (in case default init was fallback)
            self.transport_params.max_udp_payload_size = NGTCP2_DEFAULT_MAX_RECV_UDP_PAYLOAD_SIZE
            self.transport_params.ack_delay_exponent = NGTCP2_DEFAULT_ACK_DELAY_EXPONENT
            self.transport_params.max_ack_delay = NGTCP2_DEFAULT_MAX_ACK_DELAY
            
            # Set original_dcid for server (from client's first Initial packet)
            dcid_cid = ngtcp2_cid(self.dcid)
            self.transport_params.original_dcid = dcid_cid
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
                byref(self.path_storage.ps),  # path (must be non-NULL)
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
            
            # Store conn_ref pointer in SSL's app data (crypto_ossl_ctx holds SSL; get it for set_app_data)
            _ensure_openssl_bound()
            if _tls_mod.SSL_set_app_data:
                conn_ref_ptr = ctypes.cast(ctypes.pointer(self.crypto_conn_ref), c_void_p).value
                ssl_for_app_data = self.tls_session
                if getattr(_tls_mod, 'ngtcp2_crypto_ossl_ctx_get_ssl', None):
                    ssl_for_app_data = _tls_mod.ngtcp2_crypto_ossl_ctx_get_ssl(self.tls_session)
                if ssl_for_app_data:
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
            return True
            
        except Exception as e:
            logger.error(f"Error initializing ngtcp2 connection: {e}", exc_info=True)
            return False
    
    def _fill_path_storage(self):
        """Fill path_storage with local (server) and remote (client) addresses.
        ngtcp2_conn_server_new requires a non-NULL path or it crashes in ngtcp2_addr_copy.
        """
        try:
            # IPv4 sockaddr_in: family=2, port=network order, addr=network order, zero padding
            def fill_sockaddr_in(sa: ngtcp2_sockaddr_in, host: str, port: int) -> None:
                sa.sin_family = socket.AF_INET
                sa.sin_port = socket.htons(port)
                # sin_addr.s_addr is uint32 in network byte order
                addr_bytes = socket.inet_pton(socket.AF_INET, host)
                sa.sin_addr.s_addr = struct.unpack('!I', addr_bytes)[0]
                # sin_zero already zeroed by ctypes
            
            fill_sockaddr_in(self._local_sockaddr, self.server.host, self.server.port)
            fill_sockaddr_in(self._remote_sockaddr, self.remote_addr[0], self.remote_addr[1])
            
            addrlen = ctypes.sizeof(ngtcp2_sockaddr_in)
            # path expects void* (address); keep references so buffers stay alive
            self.path_storage.ps.local_addr = ctypes.cast(
                ctypes.pointer(self._local_sockaddr), c_void_p
            ).value
            self.path_storage.ps.local_addrlen = addrlen
            self.path_storage.ps.remote_addr = ctypes.cast(
                ctypes.pointer(self._remote_sockaddr), c_void_p
            ).value
            self.path_storage.ps.remote_addrlen = addrlen
        except Exception as e:
            logger.warning(f"Could not fill path storage: {e} - using zeroed path")
    
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
            return 0  # Success
        
        # Keep reference to prevent garbage collection
        self._get_new_connection_id_callback = get_new_connection_id_callback
        self.callbacks.get_new_connection_id = ctypes.cast(get_new_connection_id_callback, c_void_p).value
        
        logger.debug("ngtcp2 callbacks configured")
    
    def recv_packet(self, data: bytes, timestamp: Optional[int] = None) -> bool:
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

            # Convert data to ctypes
            pkt_data = (c_uint8 * len(data)).from_buffer_copy(data)
            
            # Read packet into connection (versioned API: 7 args; path must be non-NULL)
            result = ngtcp2_conn_read_pkt(
                self._conn_ptr,
                byref(self.path_storage.ps),
                0,  # pkt_info_version (0 = default)
                None,  # pkt_info (can be NULL)
                pkt_data,
                len(data),
                timestamp,
            )
            # ngtcp2 may set conn->log.last_ts > ts even when read_pkt returns error; ensure
            # next write_pkt gets ts strictly greater so conn_update_timestamp assertion holds
            self._last_ts = max(self._last_ts, timestamp + 1_000_000)  # advance 1ms

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
                timestamp = time.monotonic_ns()
            # ngtcp2 requires conn->log.last_ts <= ts (monotonic); clamp to last used ts
            timestamp = max(timestamp, getattr(self, '_last_ts', self.settings.initial_ts))
            self._last_ts = timestamp

            # Write packets (ngtcp2 will call our send callback)
            # We need to call ngtcp2_conn_write_pkt in a loop until no more packets
            max_packets = MAX_PKT_BURST
            packets_sent = 0
            
            while packets_sent < max_packets:
                # Allocate buffer for packet
                pkt_buf = (c_uint8 * MAX_UDP_PAYLOAD_SIZE)()
                pktlen = c_size_t(0)
                
                # Write packet (unified 6-arg API: conn, path, dest, destlen, pdatalen, ts)
                result = ngtcp2_conn_write_pkt(
                    self._conn_ptr,
                    byref(self.path_storage.ps),
                    pkt_buf,
                    MAX_UDP_PAYLOAD_SIZE,
                    byref(pktlen),
                    timestamp,
                )
                
                if result < 0:
                    # Error (negative ngtcp2_ssize)
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
                timestamp = time.monotonic_ns()
            
            # Get expiry time
            if ngtcp2_conn_get_expiry:
                expiry = ngtcp2_conn_get_expiry(self._conn_ptr)
                if expiry != 0xFFFFFFFFFFFFFFFF:  # UINT64_MAX
                    if expiry <= timestamp:
                        # Handle expiry
                        result = ngtcp2_conn_handle_expiry(
                            self._conn_ptr,
                            byref(self.path_storage.ps),
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
            timestamp = time.monotonic_ns()
            if ngtcp2_conn_close:
                # ngtcp2_conn_close is a wrapper that handles packet sending
                result = ngtcp2_conn_close(
                    self._conn_ptr,
                    byref(self.path_storage.ps),
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
            timestamp = time.monotonic_ns()
            if conn.recv_packet(data, timestamp):
                # Try to send any pending packets
                conn.send_packets(timestamp)
            else:
                logger.warning(f"Failed to process packet for connection {dcid.hex()[:8]}")
                # Remove connection so maintenance never calls send_packets (avoids ngtcp2
                # conn_update_timestamp assertion when conn->log.last_ts > ts after read_pkt error)
                if dcid in self.connections:
                    self.connections.pop(dcid)
                    conn.cleanup()

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
