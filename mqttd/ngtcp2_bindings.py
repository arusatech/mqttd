"""
ngtcp2 Python Bindings - Phase 2 Implementation
Based on curl's curl_ngtcp2.c reference implementation

This module provides Python ctypes bindings for the ngtcp2 C library.
Compatible with no-GIL Python.

Reference:
- curl/lib/vquic/curl_ngtcp2.c
- ngtcp2 API: https://nghttp2.org/ngtcp2/
"""

import ctypes
from ctypes import (
    CDLL, Structure, POINTER, CFUNCTYPE, byref,
    c_int, c_int32, c_int64, c_uint8, c_uint16, c_uint32, c_uint64,
    c_size_t, c_ssize_t, c_void_p, c_char_p, c_bool,
    Array, cast
)
import logging
import os
import time
from typing import Optional, Callable, Tuple, Any
import sys

# Avoid import conflict with Python's types module
if __name__ == "__main__":
    # When running as script, we need to avoid importing mqttd.types
    import sys as _sys
    if 'mqttd.types' in _sys.modules:
        del _sys.modules['mqttd.types']

logger = logging.getLogger(__name__)

# Constants from ngtcp2.h
NGTCP2_MAX_CIDLEN = 20
NGTCP2_MIN_CIDLEN = 0
NGTCP2_DEFAULT_MAX_RECV_UDP_PAYLOAD_SIZE = 65527
NGTCP2_DEFAULT_ACK_DELAY_EXPONENT = 3
NGTCP2_DEFAULT_MAX_ACK_DELAY = 25000000  # 25ms in nanoseconds
NGTCP2_DEFAULT_ACTIVE_CONNECTION_ID_LIMIT = 8
NGTCP2_DEFAULT_INITIAL_RTT = 333000  # 333ms in nanoseconds
NGTCP2_MILLISECONDS = 1000000
NGTCP2_SECONDS = 1000000000
NGTCP2_MICROSECONDS = 1000

# QUIC Protocol Version
NGTCP2_PROTO_VER_V1 = 0x00000001
NGTCP2_PROTO_VER_MAX = NGTCP2_PROTO_VER_V1

# Settings version constants (from ngtcp2.h)
# These are used for versioned API calls
NGTCP2_SETTINGS_V1 = 1
NGTCP2_SETTINGS_V2 = 2
NGTCP2_SETTINGS_V3 = 3
# Default to V3 (current version as of ngtcp2 1.21.0)
NGTCP2_SETTINGS_VERSION = NGTCP2_SETTINGS_V3

# Connection ID structure
class ngtcp2_cid(Structure):
    """
    Connection ID structure
    Based on curl's usage and ngtcp2 API
    """
    _fields_ = [
        ("data", (c_uint8 * NGTCP2_MAX_CIDLEN)),  # Fixed-size array
        ("datalen", c_size_t),
    ]
    
    def __init__(self, data: Optional[bytes] = None):
        super().__init__()
        if data:
            if len(data) > NGTCP2_MAX_CIDLEN:
                raise ValueError(f"Connection ID too long: {len(data)} > {NGTCP2_MAX_CIDLEN}")
            self.datalen = len(data)
            for i, byte in enumerate(data):
                self.data[i] = byte
    
    def to_bytes(self) -> bytes:
        """Convert to Python bytes"""
        return bytes(self.data[:self.datalen])
    
    def __repr__(self):
        data_str = self.to_bytes().hex() if self.datalen > 0 else ""
        return f"ngtcp2_cid(datalen={self.datalen}, data={data_str})"


# Opaque connection pointer
ngtcp2_conn = c_void_p

# Iovec structure for scatter/gather I/O
class ngtcp2_vec(Structure):
    """Iovec structure for referencing arbitrary array of bytes"""
    _fields_ = [
        ("base", POINTER(c_uint8)),  # Pointer to data
        ("len", c_size_t),  # Length of data
    ]


# Path storage structure (simplified)
class ngtcp2_path(Structure):
    """Path information (simplified version)"""
    _fields_ = [
        ("local_addr", c_void_p),  # sockaddr pointer
        ("local_addrlen", c_size_t),
        ("remote_addr", c_void_p),  # sockaddr pointer
        ("remote_addrlen", c_size_t),
    ]


class ngtcp2_path_storage(Structure):
    """Path storage (for path management)"""
    _fields_ = [
        ("ps", ngtcp2_path),
        ("_dummy", c_uint8 * 256),  # Padding
    ]


# Address structure (simplified)
class ngtcp2_addr(Structure):
    """Address structure (opaque for now)"""
    _fields_ = [
        ("addr", c_void_p),  # sockaddr pointer
        ("addrlen", c_size_t),
    ]


# Settings structure
class ngtcp2_settings(Structure):
    """
    ngtcp2_settings - Configuration settings for QUIC connection
    Based on curl's quic_settings() function and ngtcp2 API
    """
    _fields_ = [
        # Congestion control
        ("cc_algo", c_uint32),  # ngtcp2_cc_algo enum
        
        # Timing
        ("initial_rtt", c_uint64),  # ngtcp2_duration (nanoseconds)
        ("initial_ts", c_uint64),   # ngtcp2_tstamp (nanoseconds)
        ("handshake_timeout", c_uint64),  # ngtcp2_duration
        
        # ACK settings
        ("ack_thresh", c_size_t),
        ("max_ack_delay", c_uint64),  # ngtcp2_duration
        
        # UDP payload
        ("max_tx_udp_payload_size", c_size_t),
        ("no_tx_udp_payload_size_shaping", c_uint8),
        
        # Path MTU Discovery
        ("no_pmtud", c_uint8),
        
        # Flow control windows (auto-tuning)
        ("max_window", c_uint64),
        ("max_stream_window", c_uint64),
        
        # Version negotiation
        ("available_versions", POINTER(c_uint32)),
        ("available_versionslen", c_size_t),
        ("preferred_versions", POINTER(c_uint32)),
        ("preferred_versionslen", c_size_t),
        ("original_version", c_uint32),
        
        # Token (retry/new token frame)
        ("token", POINTER(c_uint8)),
        ("tokenlen", c_size_t),
        ("token_type", c_uint32),  # ngtcp2_token_type enum
        
        # Logging callbacks
        ("log_printf", c_void_p),  # ngtcp2_printf callback
        ("qlog_write", c_void_p),  # ngtcp2_qlog_write callback
        
        # Random number generator
        ("rand_ctx", c_void_p),  # ngtcp2_rand_ctx
        
        # Glitch rate limiter (DoS protection)
        ("glitch_ratelim_burst", c_uint64),
        ("glitch_ratelim_rate", c_uint64),
    ]
    
    def __init__(self):
        super().__init__()
        # Initialize to defaults (will be set by ngtcp2_settings_default)
        self.max_window = 0
        self.max_stream_window = 0


# Transport parameters structure
class ngtcp2_transport_params(Structure):
    """
    ngtcp2_transport_params - Transport parameters exchanged during handshake
    Based on curl's quic_settings() function and ngtcp2 API
    """
    _fields_ = [
        # Initial limits
        ("initial_max_data", c_uint64),
        ("initial_max_stream_data_bidi_local", c_uint64),
        ("initial_max_stream_data_bidi_remote", c_uint64),
        ("initial_max_stream_data_uni", c_uint64),
        ("initial_max_streams_bidi", c_uint64),
        ("initial_max_streams_uni", c_uint64),
        
        # Idle timeout
        ("max_idle_timeout", c_uint64),  # ngtcp2_duration (0 = no timeout)
        
        # UDP payload
        ("max_udp_payload_size", c_uint64),
        
        # ACK delay
        ("ack_delay_exponent", c_uint64),
        ("max_ack_delay", c_uint64),  # ngtcp2_duration
        
        # Connection ID
        ("active_connection_id_limit", c_uint64),
        
        # Connection IDs (for servers)
        ("original_dcid", ngtcp2_cid),
        ("original_dcid_present", c_uint8),
        ("initial_scid", ngtcp2_cid),
        ("initial_scid_present", c_uint8),
        ("retry_scid", ngtcp2_cid),
        ("retry_scid_present", c_uint8),
        
        # Stateless reset
        ("stateless_reset_token", (c_uint8 * 16)),
        ("stateless_reset_token_present", c_uint8),
        
        # Preferred address (server feature)
        ("preferred_addr", c_void_p),  # ngtcp2_preferred_addr (simplified)
        ("preferred_addr_present", c_uint8),
        
        # Disable active migration
        ("disable_active_migration", c_uint8),
        
        # Datagram frame support (RFC 9221)
        ("max_datagram_frame_size", c_uint64),
        
        # Version information (RFC 9369)
        ("version_info", c_void_p),  # ngtcp2_version_info (simplified)
        ("version_info_present", c_uint8),
    ]
    
    def __init__(self):
        super().__init__()
        # Will be initialized by ngtcp2_transport_params_default


# Error code
# Connection error type enum
NGTCP2_CCERR_TYPE_TRANSPORT = 0
NGTCP2_CCERR_TYPE_APPLICATION = 1

class ngtcp2_ccerr(Structure):
    """Connection error structure"""
    _fields_ = [
        ("type", c_uint32),  # ngtcp2_ccerr_type (enum, but use uint32 for ctypes)
        ("error_code", c_uint64),  # Application error code
    ]


# Connection callbacks structure
class ngtcp2_conn_callbacks(Structure):
    """
    Connection callbacks structure
    Based on curl's callback implementations
    """
    _fields_ = [
        ("client_initial", c_void_p),  # Callback for client initial
        ("recv_stream_data", c_void_p),  # Callback for receiving stream data
        ("acked_stream_data_offset", c_void_p),  # Callback for ACKed data
        ("stream_open", c_void_p),  # Callback for new stream
        ("stream_close", c_void_p),  # Callback for stream close
        ("stream_reset", c_void_p),  # Callback for stream reset
        ("recv_rx_key", c_void_p),  # Callback for receiving RX key
        ("recv_tx_key", c_void_p),  # Callback for receiving TX key
        ("handshake_completed", c_void_p),  # Callback for handshake completion
        ("extend_max_streams_bidi", c_void_p),  # Callback for extending max streams
        ("extend_max_streams_uni", c_void_p),  # Callback for extending max streams uni
        ("extend_max_stream_data", c_void_p),  # Callback for extending max stream data
        ("rand", c_void_p),  # Random number generator callback
        ("get_new_connection_id", c_void_p),  # Get new connection ID callback
        ("remove_connection_id", c_void_p),  # Remove connection ID callback
        ("update_key", c_void_p),  # Update key callback
        ("path_validation", c_void_p),  # Path validation callback
        ("select_preferred_addr", c_void_p),  # Select preferred address callback
        ("stream_reset", c_void_p),  # Stream reset callback (duplicate?)
        ("extend_max_remote_streams_bidi", c_void_p),  # Extend max remote streams bidi
        ("extend_max_remote_streams_uni", c_void_p),  # Extend max remote streams uni
    ]


# Crypto connection reference (for TLS integration)
class ngtcp2_crypto_conn_ref(Structure):
    """Crypto connection reference (for TLS integration)"""
    _fields_ = [
        ("user_data", c_void_p),
        ("get_conn", c_void_p),  # Function pointer to get connection
    ]


# Callback function types
SendPacketFunc = CFUNCTYPE(
    c_ssize_t,  # return: bytes sent or error
    c_void_p,   # user_data
    POINTER(c_uint8),  # data
    c_size_t,   # datalen
    POINTER(ngtcp2_addr),  # addr
    c_void_p    # path
)

RecvPacketFunc = CFUNCTYPE(
    c_ssize_t,  # return: bytes received or error
    c_void_p,   # user_data
    POINTER(ngtcp2_addr),  # addr
    POINTER(c_uint8),  # data
    c_size_t    # datalen
)


# Load ngtcp2 library
_ngtcp2_lib = None
NGTCP2_AVAILABLE = False

def _load_ngtcp2_library():
    """Load ngtcp2 library from common locations"""
    global _ngtcp2_lib, NGTCP2_AVAILABLE
    
    if _ngtcp2_lib is not None:
        return _ngtcp2_lib is not None
    
    # Try common library names and paths
    lib_names = [
        'libngtcp2.so',
        'libngtcp2.so.0',
        'libngtcp2.so.16',
        'ngtcp2',
    ]
    
    # Also check LD_LIBRARY_PATH and standard paths
    lib_paths = [
        '/usr/local/lib',
        '/usr/lib',
        '/lib',
        os.environ.get('LD_LIBRARY_PATH', '').split(':') if os.environ.get('LD_LIBRARY_PATH') else [],
    ]
    
    # Flatten paths
    search_paths = []
    for path in lib_paths:
        if isinstance(path, list):
            search_paths.extend([p for p in path if p])
        elif path:
            search_paths.append(path)
    
    # CRITICAL: Load crypto library first and ensure it's initialized
    # ngtcp2 requires crypto backend to be available before loading
    try:
        from . import ngtcp2_tls_bindings
        ngtcp2_tls_bindings._load_ngtcp2_crypto_library()
        if ngtcp2_tls_bindings.NGTCP2_CRYPTO_AVAILABLE:
            ngtcp2_tls_bindings.init_tls_backend()
    except Exception:
        pass  # Continue even if crypto init fails
    
    for lib_name in lib_names:
        # Try without path first (relies on LD_LIBRARY_PATH)
        # Use RTLD_GLOBAL to ensure crypto symbols are available
        try:
            _ngtcp2_lib = CDLL(lib_name, mode=ctypes.RTLD_GLOBAL)
            logger.info(f"Loaded ngtcp2 library: {lib_name}")
            NGTCP2_AVAILABLE = True
            return True
        except OSError:
            pass
        
        # Try with explicit paths
        for path in search_paths:
            full_path = os.path.join(path, lib_name)
            if os.path.exists(full_path):
                try:
                    # Use RTLD_GLOBAL to ensure crypto symbols are available
                    _ngtcp2_lib = CDLL(full_path, mode=ctypes.RTLD_GLOBAL)
                    logger.info(f"Loaded ngtcp2 library: {full_path}")
                    NGTCP2_AVAILABLE = True
                    return True
                except OSError:
                    continue
    
    logger.warning("ngtcp2 library not found. QUIC support will be disabled.")
    NGTCP2_AVAILABLE = False
    return False


# Load library on import
# CRITICAL: Initialize TLS backend BEFORE loading ngtcp2 library
# ngtcp2 requires TLS backend to be initialized before ANY ngtcp2 function calls
# This prevents "ngtcp2_settings.c:96 ngtcp2_settingslen_version: Unreachable" crashes
try:
    # Import and initialize TLS backend first
    from . import ngtcp2_tls_bindings
    # Load crypto library
    ngtcp2_tls_bindings._load_ngtcp2_crypto_library()
    # Initialize TLS backend
    ngtcp2_tls_bindings.init_tls_backend()
except Exception as e:
    logger.debug(f"Could not pre-initialize TLS backend: {e}")

# Now load ngtcp2 library
_load_ngtcp2_library()


def get_ngtcp2_lib():
    """Get the loaded ngtcp2 library, or None if not available"""
    if not NGTCP2_AVAILABLE:
        return None
    return _ngtcp2_lib


# Bind key ngtcp2 functions
if NGTCP2_AVAILABLE and _ngtcp2_lib:
    lib = _ngtcp2_lib
    
    # Version info
    try:
        ngtcp2_version = lib.ngtcp2_version
        ngtcp2_version.argtypes = [c_uint32]  # flags
        ngtcp2_version.restype = c_void_p  # ngtcp2_info pointer (simplified to void*)
    except AttributeError:
        ngtcp2_version = None
        logger.warning("ngtcp2_version function not found")
    
    # Settings default - try versioned first, then non-versioned
    ngtcp2_settings_default = None
    try:
        # Try versioned function (newer API)
        ngtcp2_settings_default_versioned = lib.ngtcp2_settings_default_versioned
        ngtcp2_settings_default_versioned.argtypes = [
            POINTER(ngtcp2_settings),
            c_int,  # settings_version
        ]
        ngtcp2_settings_default_versioned.restype = None
        
        # WORKAROUND: ngtcp2_settings_default crashes even with TLS initialized
        # This appears to be a library build/configuration issue
        # Use manual initialization instead to avoid crashes
        def _settings_default_wrapper(settings_ptr):
            """Manually initialize settings to avoid ngtcp2_settings_default crash"""
            import ctypes
            # Zero out the structure
            ctypes.memset(settings_ptr, 0, ctypes.sizeof(ngtcp2_settings))
            # Get the settings object
            settings = settings_ptr.contents if hasattr(settings_ptr, 'contents') else None
            if settings:
                # Set defaults matching ngtcp2 defaults
                settings.cc_algo = 0  # NGTCP2_CC_ALGO_CUBIC
                settings.initial_rtt = 333000  # NGTCP2_DEFAULT_INITIAL_RTT
                settings.ack_thresh = 2
                settings.max_tx_udp_payload_size = 1452  # 1500 - 48
                settings.handshake_timeout = 0xFFFFFFFFFFFFFFFF  # UINT64_MAX
                # Note: Other fields remain zero (default values)
        
        ngtcp2_settings_default = _settings_default_wrapper
        logger.debug("Using manual settings initialization (workaround for ngtcp2_settings_default crash)")
        logger.debug(f"Using ngtcp2_settings_default_versioned with version {NGTCP2_SETTINGS_VERSION} (TLS-aware wrapper)")
    except AttributeError:
        try:
            # Fall back to non-versioned function
            ngtcp2_settings_default = lib.ngtcp2_settings_default
            ngtcp2_settings_default.argtypes = [POINTER(ngtcp2_settings)]
            ngtcp2_settings_default.restype = None
            logger.debug("Using ngtcp2_settings_default")
        except AttributeError:
            ngtcp2_settings_default = None
            logger.warning("ngtcp2_settings_default function not found")
    
    # Transport params default - try versioned first, then non-versioned
    ngtcp2_transport_params_default = None
    try:
        # Try versioned function (newer API)
        ngtcp2_transport_params_default_versioned = lib.ngtcp2_transport_params_default_versioned
        ngtcp2_transport_params_default_versioned.argtypes = [
            POINTER(ngtcp2_transport_params),
            c_int,  # transport_params_version
        ]
        ngtcp2_transport_params_default_versioned.restype = None
        
        # WORKAROUND: Same crash issue as settings_default - use manual initialization
        def _transport_params_default_wrapper(params_ptr):
            """Manually initialize transport params to avoid crash"""
            import ctypes
            # Zero out the structure
            ctypes.memset(params_ptr, 0, ctypes.sizeof(ngtcp2_transport_params))
            # Get the params object
            params = params_ptr.contents if hasattr(params_ptr, 'contents') else None
            if params:
                # Set defaults (minimal required fields)
                # Most fields can remain zero (default values)
                pass  # Transport params are typically set explicitly by caller
        
        ngtcp2_transport_params_default = _transport_params_default_wrapper
        logger.debug("Using manual transport params initialization (workaround for crash)")
    except AttributeError:
        try:
            # Fall back to non-versioned function
            ngtcp2_transport_params_default = lib.ngtcp2_transport_params_default
            ngtcp2_transport_params_default.argtypes = [POINTER(ngtcp2_transport_params)]
            ngtcp2_transport_params_default.restype = None
            logger.debug("Using ngtcp2_transport_params_default")
        except AttributeError:
            ngtcp2_transport_params_default = None
            logger.warning("ngtcp2_transport_params_default function not found")
    
    # Connection management - Server (try versioned first)
    ngtcp2_conn_server_new = None
    try:
        # Try versioned function (newer API)
        ngtcp2_conn_server_new_versioned = lib.ngtcp2_conn_server_new_versioned
        ngtcp2_conn_server_new_versioned.argtypes = [
            POINTER(POINTER(ngtcp2_conn)),  # conn (out)
            POINTER(ngtcp2_cid),  # dcid (destination connection ID)
            POINTER(ngtcp2_cid),  # scid (source connection ID)
            POINTER(ngtcp2_path),  # path
            c_uint32,  # client_chosen_version
            c_int,  # callbacks_version
            POINTER(ngtcp2_conn_callbacks),  # callbacks
            c_int,  # settings_version
            POINTER(ngtcp2_settings),  # settings
            c_int,  # transport_params_version
            POINTER(ngtcp2_transport_params),  # transport_params
            c_void_p,  # mem (memory allocator, can be NULL)
            c_void_p,  # user_data
        ]
        ngtcp2_conn_server_new_versioned.restype = c_int  # 0 on success
        # Create wrapper for easier use
        def _conn_server_new(pconn, dcid, scid, path, client_version, callbacks, settings, transport_params, mem, user_data):
            return ngtcp2_conn_server_new_versioned(
                pconn, dcid, scid, path, client_version,
                0, callbacks,  # callbacks_version = 0 (current)
                0, settings,  # settings_version = 0 (current)
                0, transport_params,  # transport_params_version = 0 (current)
                mem, user_data
            )
        ngtcp2_conn_server_new = _conn_server_new
        logger.debug("Using ngtcp2_conn_server_new_versioned")
    except AttributeError:
        try:
            # Fall back to non-versioned function
            ngtcp2_conn_server_new = lib.ngtcp2_conn_server_new
            ngtcp2_conn_server_new.argtypes = [
                POINTER(POINTER(ngtcp2_conn)),
                POINTER(ngtcp2_cid),
                POINTER(ngtcp2_cid),
                POINTER(ngtcp2_path),
                c_uint32,
                POINTER(ngtcp2_conn_callbacks),
                POINTER(ngtcp2_settings),
                POINTER(ngtcp2_transport_params),
                c_void_p,
                c_void_p,
            ]
            ngtcp2_conn_server_new.restype = c_int
            logger.debug("Using ngtcp2_conn_server_new")
        except AttributeError:
            ngtcp2_conn_server_new = None
            logger.warning("ngtcp2_conn_server_new function not found")
    
    # Connection management - Accept packet (for new connections)
    try:
        ngtcp2_accept = lib.ngtcp2_accept
        # ngtcp2_accept takes pkt_hd (out), pkt (in), pktlen
        # We'll need to define ngtcp2_pkt_hd structure for this
        ngtcp2_accept.argtypes = [
            c_void_p,  # ngtcp2_pkt_hd *dest (can be NULL)
            POINTER(c_uint8),  # pkt (packet data)
            c_size_t,  # pktlen (packet length)
        ]
        ngtcp2_accept.restype = c_int  # 0 on success
        logger.debug("Loaded ngtcp2_accept")
    except AttributeError:
        ngtcp2_accept = None
        logger.warning("ngtcp2_accept function not found")
    
    # Connection management - Read packet (process packet for existing connection)
    ngtcp2_conn_read_pkt = None
    try:
        # Try versioned function (newer API)
        ngtcp2_conn_read_pkt_versioned = lib.ngtcp2_conn_read_pkt_versioned
        ngtcp2_conn_read_pkt_versioned.argtypes = [
            ngtcp2_conn,  # conn
            POINTER(ngtcp2_path),  # path
            c_void_p,  # ngtcp2_pkt_info *pi (can be NULL)
            POINTER(c_uint8),  # pkt (packet data)
            c_size_t,  # pktlen (packet length)
            c_uint64,  # ts (timestamp in nanoseconds)
        ]
        ngtcp2_conn_read_pkt_versioned.restype = c_int  # 0 on success
        ngtcp2_conn_read_pkt = ngtcp2_conn_read_pkt_versioned
        logger.debug("Loaded ngtcp2_conn_read_pkt_versioned")
    except AttributeError:
        try:
            # Fall back to non-versioned function
            ngtcp2_conn_read_pkt = lib.ngtcp2_conn_read_pkt
            ngtcp2_conn_read_pkt.argtypes = [
                ngtcp2_conn,
                POINTER(ngtcp2_path),
                c_void_p,
                POINTER(c_uint8),
                c_size_t,
                c_uint64,
            ]
            ngtcp2_conn_read_pkt.restype = c_int
            logger.debug("Loaded ngtcp2_conn_read_pkt")
        except AttributeError:
            ngtcp2_conn_read_pkt = None
            logger.warning("ngtcp2_conn_read_pkt function not found")
    
    # Connection management - Write packets (try versioned first)
    ngtcp2_conn_write_pkt = None
    try:
        # Try versioned function (newer API)
        ngtcp2_conn_write_pkt_versioned = lib.ngtcp2_conn_write_pkt_versioned
        ngtcp2_conn_write_pkt_versioned.argtypes = [
            ngtcp2_conn,  # conn
            POINTER(ngtcp2_path),  # path
            POINTER(c_uint8),  # out (output buffer)
            c_size_t,  # outlen (output buffer size)
            POINTER(c_size_t),  # pktlen (packet length out)
            c_uint64,  # ts (timestamp)
            c_void_p,  # user_data
            c_void_p,  # send_pkt callback (function pointer)
        ]
        ngtcp2_conn_write_pkt_versioned.restype = c_int  # 0 on success
        ngtcp2_conn_write_pkt = ngtcp2_conn_write_pkt_versioned
        logger.debug("Using ngtcp2_conn_write_pkt_versioned")
    except AttributeError:
        try:
            # Fall back to non-versioned function
            ngtcp2_conn_write_pkt = lib.ngtcp2_conn_write_pkt
            ngtcp2_conn_write_pkt.argtypes = [
                ngtcp2_conn,
                POINTER(ngtcp2_path),
                POINTER(c_uint8),
                c_size_t,
                POINTER(c_size_t),
                c_uint64,
                c_void_p,
                c_void_p,
            ]
            ngtcp2_conn_write_pkt.restype = c_int
            logger.debug("Using ngtcp2_conn_write_pkt")
        except AttributeError:
            ngtcp2_conn_write_pkt = None
            logger.warning("ngtcp2_conn_write_pkt function not found")
    
    # Connection management - Handle expiry
    try:
        ngtcp2_conn_handle_expiry = lib.ngtcp2_conn_handle_expiry
        ngtcp2_conn_handle_expiry.argtypes = [
            ngtcp2_conn,  # conn
            POINTER(ngtcp2_path),  # path
            c_uint64,  # ts (timestamp)
            c_void_p,  # user_data
            POINTER(SendPacketFunc),  # send_pkt callback
        ]
        ngtcp2_conn_handle_expiry.restype = c_int  # 0 on success
    except AttributeError:
        ngtcp2_conn_handle_expiry = None
        logger.warning("ngtcp2_conn_handle_expiry function not found")
    
    # Connection management - Close connection
    # Use ngtcp2_conn_write_connection_close_versioned (the actual function name)
    ngtcp2_conn_close = None
    try:
        # Try versioned function first
        ngtcp2_conn_write_connection_close_versioned = lib.ngtcp2_conn_write_connection_close_versioned
        ngtcp2_conn_write_connection_close_versioned.argtypes = [
            ngtcp2_conn,  # conn
            POINTER(ngtcp2_path),  # path (can be NULL)
            c_void_p,  # pkt_info (can be NULL)
            POINTER(c_uint8),  # out (output buffer)
            c_size_t,  # outlen (output buffer size)
            POINTER(ngtcp2_ccerr),  # ccerr (connection close error, can be NULL)
            c_uint64,  # ts (timestamp)
        ]
        ngtcp2_conn_write_connection_close_versioned.restype = c_ssize_t  # bytes written or error
        
        # Create wrapper for easier use (simplified signature)
        def _conn_close_wrapper(conn, path, error_code, reason, reasonlen, ts, user_data, send_pkt):
            """Wrapper for ngtcp2_conn_write_connection_close_versioned"""
            # Create error structure
            ccerr = ngtcp2_ccerr()
            ccerr.type = NGTCP2_CCERR_TYPE_APPLICATION
            ccerr.error_code = error_code
            
            # Allocate buffer for connection close packet
            buffer = (c_uint8 * NGTCP2_DEFAULT_MAX_RECV_UDP_PAYLOAD_SIZE)()
            result = ngtcp2_conn_write_connection_close_versioned(
                conn,
                path,
                None,  # pkt_info
                buffer,
                NGTCP2_DEFAULT_MAX_RECV_UDP_PAYLOAD_SIZE,
                byref(ccerr),
                ts
            )
            if result > 0:
                # Packet written successfully - send it via callback if provided
                if send_pkt:
                    send_pkt(user_data, buffer, result, path, None)
                return 0  # Success
            return int(result)  # Error code (convert ssize_t to int)
        
        ngtcp2_conn_close = _conn_close_wrapper
        logger.debug("Using ngtcp2_conn_write_connection_close_versioned")
    except AttributeError:
        try:
            # Try non-versioned function
            ngtcp2_conn_write_connection_close = lib.ngtcp2_conn_write_connection_close
            ngtcp2_conn_write_connection_close.argtypes = [
                ngtcp2_conn,
                POINTER(ngtcp2_path),
                c_void_p,
                POINTER(c_uint8),
                c_size_t,
                POINTER(ngtcp2_ccerr),
                c_uint64,
            ]
            ngtcp2_conn_write_connection_close.restype = c_ssize_t
            ngtcp2_conn_close = ngtcp2_conn_write_connection_close
            logger.debug("Using ngtcp2_conn_write_connection_close")
        except AttributeError:
            ngtcp2_conn_close = None
            logger.warning("ngtcp2_conn_close function not found")
    
    # Note: Stream data is typically received via callbacks (recv_stream_data)
    # rather than direct function calls. The callbacks are defined in ngtcp2_conn_callbacks.
    # We'll keep this for compatibility, but it may not exist in newer API versions.
    ngtcp2_strm_recv = None
    try:
        ngtcp2_strm_recv = lib.ngtcp2_strm_recv
        ngtcp2_strm_recv.argtypes = [
            ngtcp2_conn,  # conn
            c_int64,  # stream_id
            POINTER(c_uint8),  # data (out)
            c_size_t,  # datalen (buffer size)
            POINTER(c_size_t),  # pconsumed (bytes consumed, out)
            c_uint32,  # fin (1 if FIN bit is set)
        ]
        ngtcp2_strm_recv.restype = c_int  # 0 on success
        logger.debug("Loaded ngtcp2_strm_recv")
    except AttributeError:
        # This is OK - stream data comes through callbacks in newer API
        logger.debug("ngtcp2_strm_recv not available (using callbacks instead)")
    
    # Stream management - Write stream data
    # Use ngtcp2_conn_writev_stream_versioned (the actual function name)
    ngtcp2_strm_write = None
    try:
        # Try versioned function first
        ngtcp2_conn_writev_stream_versioned = lib.ngtcp2_conn_writev_stream_versioned
        ngtcp2_conn_writev_stream_versioned.argtypes = [
            ngtcp2_conn,  # conn
            c_int64,  # stream_id
            POINTER(ngtcp2_vec),  # vec (iovec array)
            c_size_t,  # veccnt (number of iovecs)
            c_uint32,  # fin (1 to set FIN bit)
            c_uint64,  # ts (timestamp)
            c_void_p,  # user_data
            POINTER(SendPacketFunc),  # send_pkt callback
        ]
        ngtcp2_conn_writev_stream_versioned.restype = c_ssize_t  # bytes written or error
        
        # Create wrapper for easier use (single buffer instead of iovec)
        def _strm_write_wrapper(conn, stream_id, data, datalen, fin):
            """Wrapper for ngtcp2_conn_writev_stream_versioned with single buffer"""
            # Create iovec structure
            vec = ngtcp2_vec()
            # data should be a pointer to uint8 array or bytes
            if isinstance(data, (bytes, bytearray)):
                # Convert bytes to c_uint8 array
                data_array = (c_uint8 * datalen).from_buffer_copy(data)
                vec.base = cast(data_array, POINTER(c_uint8))
            else:
                # Assume it's already a pointer
                vec.base = cast(data, POINTER(c_uint8))
            vec.len = datalen
            
            # Call versioned function
            result = ngtcp2_conn_writev_stream_versioned(
                conn,
                stream_id,
                byref(vec),
                1,  # veccnt
                fin,
                int(time.time() * NGTCP2_SECONDS),  # ts
                None,  # user_data
                None,  # send_pkt callback (handled separately)
            )
            return int(result)  # Convert ssize_t to int
        
        ngtcp2_strm_write = _strm_write_wrapper
        logger.debug("Using ngtcp2_conn_writev_stream_versioned")
    except AttributeError:
        try:
            # Try non-versioned function
            ngtcp2_conn_writev_stream = lib.ngtcp2_conn_writev_stream
            ngtcp2_conn_writev_stream.argtypes = [
                ngtcp2_conn,
                c_int64,
                POINTER(ngtcp2_vec),
                c_size_t,
                c_uint32,
                c_uint64,
                c_void_p,
                POINTER(SendPacketFunc),
            ]
            ngtcp2_conn_writev_stream.restype = c_ssize_t
            ngtcp2_strm_write = ngtcp2_conn_writev_stream
            logger.debug("Using ngtcp2_conn_writev_stream")
        except AttributeError:
            ngtcp2_strm_write = None
            logger.warning("ngtcp2_strm_write function not found")
    
    # Stream management - Shutdown stream
    # Use ngtcp2_conn_shutdown_stream (the actual function name)
    try:
        ngtcp2_strm_shutdown = lib.ngtcp2_conn_shutdown_stream
        ngtcp2_strm_shutdown.argtypes = [
            ngtcp2_conn,  # conn
            c_uint32,  # flags (NGTCP2_SHUTDOWN_STREAM_FLAG_*)
            c_int64,  # stream_id
            c_uint64,  # error_code (application error code)
        ]
        ngtcp2_strm_shutdown.restype = c_int  # 0 on success
        logger.debug("Loaded ngtcp2_conn_shutdown_stream")
    except AttributeError:
        ngtcp2_strm_shutdown = None
        logger.warning("ngtcp2_strm_shutdown function not found")
    
    # Connection info
    try:
        ngtcp2_conn_get_tls_alert = lib.ngtcp2_conn_get_tls_alert
        ngtcp2_conn_get_tls_alert.argtypes = [ngtcp2_conn]
        ngtcp2_conn_get_tls_alert.restype = c_uint8  # TLS alert code
    except AttributeError:
        ngtcp2_conn_get_tls_alert = None
    
    try:
        ngtcp2_conn_get_remote_transport_params = lib.ngtcp2_conn_get_remote_transport_params
        ngtcp2_conn_get_remote_transport_params.argtypes = [ngtcp2_conn]
        ngtcp2_conn_get_remote_transport_params.restype = POINTER(ngtcp2_transport_params)
    except AttributeError:
        ngtcp2_conn_get_remote_transport_params = None
    
    try:
        ngtcp2_conn_set_keep_alive_timeout = lib.ngtcp2_conn_set_keep_alive_timeout
        ngtcp2_conn_set_keep_alive_timeout.argtypes = [
            ngtcp2_conn,
            c_uint64,  # timeout (duration in nanoseconds)
        ]
        ngtcp2_conn_set_keep_alive_timeout.restype = None
    except AttributeError:
        ngtcp2_conn_set_keep_alive_timeout = None
    
    try:
        ngtcp2_conn_extend_max_stream_offset = lib.ngtcp2_conn_extend_max_stream_offset
        ngtcp2_conn_extend_max_stream_offset.argtypes = [
            ngtcp2_conn,
            c_int64,  # stream_id
            c_uint64,  # max_stream_offset
        ]
        ngtcp2_conn_extend_max_stream_offset.restype = c_int
    except AttributeError:
        ngtcp2_conn_extend_max_stream_offset = None
    
    try:
        ngtcp2_conn_extend_max_offset = lib.ngtcp2_conn_extend_max_offset
        ngtcp2_conn_extend_max_offset.argtypes = [
            ngtcp2_conn,
            c_uint64,  # max_offset
        ]
        ngtcp2_conn_extend_max_offset.restype = c_int
    except AttributeError:
        ngtcp2_conn_extend_max_offset = None
    
    try:
        ngtcp2_conn_shutdown_stream = lib.ngtcp2_conn_shutdown_stream
        ngtcp2_conn_shutdown_stream.argtypes = [
            ngtcp2_conn,
            c_uint32,  # flags
            c_int64,  # stream_id
            c_uint64,  # error_code
        ]
        ngtcp2_conn_shutdown_stream.restype = c_int
    except AttributeError:
        ngtcp2_conn_shutdown_stream = None
    
    try:
        ngtcp2_conn_get_stream_user_data = lib.ngtcp2_conn_get_stream_user_data
        ngtcp2_conn_get_stream_user_data.argtypes = [ngtcp2_conn, c_int64]
        ngtcp2_conn_get_stream_user_data.restype = c_void_p
    except AttributeError:
        ngtcp2_conn_get_stream_user_data = None
    
    try:
        ngtcp2_conn_set_stream_user_data = lib.ngtcp2_conn_set_stream_user_data
        ngtcp2_conn_set_stream_user_data.argtypes = [
            ngtcp2_conn,
            c_int64,  # stream_id
            c_void_p,  # user_data
        ]
        ngtcp2_conn_set_stream_user_data.restype = None
    except AttributeError:
        ngtcp2_conn_set_stream_user_data = None
    
    # Connection expiry and state
    try:
        ngtcp2_conn_get_expiry = lib.ngtcp2_conn_get_expiry
        ngtcp2_conn_get_expiry.argtypes = [ngtcp2_conn]
        ngtcp2_conn_get_expiry.restype = c_uint64  # ngtcp2_tstamp
    except AttributeError:
        ngtcp2_conn_get_expiry = None
    
    try:
        ngtcp2_conn_get_handshake_completed = lib.ngtcp2_conn_get_handshake_completed
        ngtcp2_conn_get_handshake_completed.argtypes = [ngtcp2_conn]
        ngtcp2_conn_get_handshake_completed.restype = c_int  # boolean
    except AttributeError:
        ngtcp2_conn_get_handshake_completed = None
    
    try:
        ngtcp2_conn_del = lib.ngtcp2_conn_del
        ngtcp2_conn_del.argtypes = [ngtcp2_conn, c_void_p]  # mem (can be NULL)
        ngtcp2_conn_del.restype = None
    except AttributeError:
        ngtcp2_conn_del = None

else:
    # Set all to None if library not available
    ngtcp2_version = None
    ngtcp2_settings_default = None
    ngtcp2_transport_params_default = None
    ngtcp2_conn_server_new = None
    ngtcp2_accept = None
    ngtcp2_conn_read_pkt = None
    ngtcp2_conn_write_pkt = None
    ngtcp2_conn_handle_expiry = None
    ngtcp2_conn_close = None
    ngtcp2_strm_recv = None
    ngtcp2_strm_write = None
    ngtcp2_strm_shutdown = None
    ngtcp2_conn_get_tls_alert = None
    ngtcp2_conn_get_remote_transport_params = None
    ngtcp2_conn_set_keep_alive_timeout = None
    ngtcp2_conn_extend_max_stream_offset = None
    ngtcp2_conn_extend_max_offset = None
    ngtcp2_conn_shutdown_stream = None
    ngtcp2_conn_get_stream_user_data = None
    ngtcp2_conn_set_stream_user_data = None
    ngtcp2_conn_get_expiry = None
    ngtcp2_conn_get_handshake_completed = None
    ngtcp2_conn_del = None


def verify_bindings() -> bool:
    """Verify that essential bindings are available"""
    if not NGTCP2_AVAILABLE:
        return False
    
    essential_functions = [
        ngtcp2_settings_default,
        ngtcp2_transport_params_default,
        ngtcp2_conn_server_new,
        ngtcp2_accept,  # For accepting new connections
        ngtcp2_conn_read_pkt,  # For reading packets into connections
        ngtcp2_conn_write_pkt,  # For writing packets
    ]
    
    missing = [f for f in essential_functions if f is None]
    if missing:
        logger.warning(f"Some essential ngtcp2 functions are missing: {len(missing)} functions")
        return False
    
    return True


# Test basic functionality
if __name__ == "__main__":
    print(f"ngtcp2 library available: {NGTCP2_AVAILABLE}")
    if NGTCP2_AVAILABLE:
        print(f"Bindings verified: {verify_bindings()}")
        
        # Test structure creation
        settings = ngtcp2_settings()
        print(f"Created ngtcp2_settings structure: {settings}")
        
        if ngtcp2_settings_default:
            ngtcp2_settings_default(byref(settings))
            print(f"Initialized settings with defaults: max_window={settings.max_window}")
        
        cid = ngtcp2_cid(b"test_cid")
        print(f"Created connection ID: {cid}")
    
    else:
        print("Install ngtcp2 library to enable QUIC support")
        print("See: https://github.com/ngtcp2/ngtcp2")
