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
# Minimum UDP payload size; settings->max_tx_udp_payload_size must be >= this (ngtcp2_conn.c assertion)
NGTCP2_MAX_UDP_PAYLOAD_SIZE = 1200
NGTCP2_DEFAULT_MAX_RECV_UDP_PAYLOAD_SIZE = 65527
NGTCP2_DEFAULT_ACK_DELAY_EXPONENT = 3
NGTCP2_DEFAULT_MAX_ACK_DELAY = 25000000  # 25ms in nanoseconds
NGTCP2_DEFAULT_ACTIVE_CONNECTION_ID_LIMIT = 2  # From ngtcp2.h: default value if omitted
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

# Callbacks version constants (from ngtcp2_callbacks.h / ngtcp2_callbacks.c)
# ngtcp2_callbackslen_version() only accepts these; 0 triggers Unreachable abort
NGTCP2_CALLBACKS_V1 = 1
NGTCP2_CALLBACKS_VERSION = 2  # current layout (full struct)

# Transport params version (from ngtcp2.h: NGTCP2_TRANSPORT_PARAMS_VERSION = V1)
NGTCP2_TRANSPORT_PARAMS_V1 = 1
NGTCP2_TRANSPORT_PARAMS_VERSION = NGTCP2_TRANSPORT_PARAMS_V1

# Packet info version (from ngtcp2.h: NGTCP2_PKT_INFO_V1)
NGTCP2_PKT_INFO_V1 = 1

# Connection ID structure - FIELD ORDER MUST MATCH C: datalen FIRST, then data
class ngtcp2_cid(Structure):
    """
    Connection ID structure - C layout:
      typedef struct ngtcp2_cid {
        size_t datalen;              // FIRST
        uint8_t data[NGTCP2_MAX_CIDLEN];  // SECOND
      } ngtcp2_cid;
    """
    _fields_ = [
        ("datalen", c_size_t),                        # 8 bytes on 64-bit
        ("data", (c_uint8 * NGTCP2_MAX_CIDLEN)),      # 20 bytes
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


# Packet info (from ngtcp2.h: ngtcp2_pkt_info, used by read_pkt/write_pkt versioned APIs)
class ngtcp2_pkt_info(Structure):
    """Packet metadata (ECN etc.). Used when calling read_pkt_versioned / write_pkt_versioned."""
    _fields_ = [("ecn", c_uint8)]


# Iovec structure for scatter/gather I/O
class ngtcp2_vec(Structure):
    """Iovec structure for referencing arbitrary array of bytes"""
    _fields_ = [
        ("base", POINTER(c_uint8)),  # Pointer to data
        ("len", c_size_t),  # Length of data
    ]


# Path storage structure - matches ngtcp2.h exactly
class ngtcp2_addr(Structure):
    """Address structure - pointer to sockaddr + length"""
    _fields_ = [
        ("addr", c_void_p),  # sockaddr pointer
        ("addrlen", c_size_t),
    ]


class ngtcp2_path(Structure):
    """Network path - local and remote addresses"""
    _fields_ = [
        ("local", ngtcp2_addr),
        ("remote", ngtcp2_addr),
        ("user_data", c_void_p),
    ]


class ngtcp2_sockaddr_union(Structure):
    """Union for sockaddr storage (128 bytes per ngtcp2.h)"""
    _fields_ = [("data", c_uint8 * 128)]


class ngtcp2_path_storage(Structure):
    """Path storage with embedded sockaddr buffers"""
    _fields_ = [
        ("path", ngtcp2_path),
        ("local_addrbuf", ngtcp2_sockaddr_union),
        ("remote_addrbuf", ngtcp2_sockaddr_union),
    ]


# Settings structure - field order MUST match ngtcp2.h ngtcp2_settings (conn_new assertion)
class ngtcp2_rand_ctx(Structure):
    """Opaque wrapper for ngtcp2_rand_ctx (single pointer)."""
    _fields_ = [("native_handle", c_void_p)]


class ngtcp2_settings(Structure):
    """
    ngtcp2_settings - Field order must match C struct in ngtcp2.h exactly,
    else conn_new asserts (e.g. max_tx_udp_payload_size >= NGTCP2_MAX_UDP_PAYLOAD_SIZE).
    """
    _fields_ = [
        ("qlog_write", c_void_p),
        ("cc_algo", c_uint32),
        ("_pad1", c_uint32),
        ("initial_ts", c_uint64),
        ("initial_rtt", c_uint64),
        ("log_printf", c_void_p),
        ("max_tx_udp_payload_size", c_size_t),
        ("token", POINTER(c_uint8)),
        ("tokenlen", c_size_t),
        ("token_type", c_uint32),
        ("_pad2", c_uint32),
        ("rand_ctx", ngtcp2_rand_ctx),
        ("max_window", c_uint64),
        ("max_stream_window", c_uint64),
        ("ack_thresh", c_size_t),
        ("no_tx_udp_payload_size_shaping", c_uint8),
        ("_pad3", c_uint8 * 7),
        ("handshake_timeout", c_uint64),
        ("preferred_versions", POINTER(c_uint32)),
        ("preferred_versionslen", c_size_t),
        ("available_versions", POINTER(c_uint32)),
        ("available_versionslen", c_size_t),
        ("original_version", c_uint32),
        ("no_pmtud", c_uint8),
        ("_pad5", c_uint8 * 3),
        ("initial_pkt_num", c_uint32),
        ("pmtud_probes", POINTER(c_uint16)),
        ("pmtud_probeslen", c_size_t),
        ("glitch_ratelim_burst", c_uint64),
        ("glitch_ratelim_rate", c_uint64),
    ]
    
    def __init__(self):
        super().__init__()
        self.max_window = 0
        self.max_stream_window = 0


# Socket address structures for ngtcp2_preferred_addr
class ngtcp2_in_addr(Structure):
    """IPv4 address (4 bytes) - stored as bytes in network byte order"""
    _fields_ = [("s_addr", c_uint8 * 4)]


class ngtcp2_sockaddr_in(Structure):
    """IPv4 socket address - matches struct sockaddr_in (16 bytes)"""
    _fields_ = [
        ("sin_family", c_uint16),
        ("sin_port", c_uint16),
        ("sin_addr", ngtcp2_in_addr),
        ("sin_zero", c_uint8 * 8),
    ]


class ngtcp2_in6_addr(Structure):
    """IPv6 address (16 bytes)"""
    _fields_ = [("in6_addr", c_uint8 * 16)]


class ngtcp2_sockaddr_in6(Structure):
    """IPv6 socket address - matches struct sockaddr_in6 (28 bytes)"""
    _fields_ = [
        ("sin6_family", c_uint16),
        ("sin6_port", c_uint16),
        ("sin6_flowinfo", c_uint32),
        ("sin6_addr", ngtcp2_in6_addr),
        ("sin6_scope_id", c_uint32),
    ]


# Transport parameters structure - field order MUST match ngtcp2.h (conn_new assertion)
class ngtcp2_preferred_addr(Structure):
    """
    Preferred address - C layout:
      typedef struct ngtcp2_preferred_addr {
        ngtcp2_cid cid;
        ngtcp2_sockaddr_in ipv4;
        ngtcp2_sockaddr_in6 ipv6;
        uint8_t ipv4_present;
        uint8_t ipv6_present;
        uint8_t stateless_reset_token[16];
      } ngtcp2_preferred_addr;
    """
    _fields_ = [
        ("cid", ngtcp2_cid),
        ("ipv4", ngtcp2_sockaddr_in),
        ("ipv6", ngtcp2_sockaddr_in6),
        ("ipv4_present", c_uint8),
        ("ipv6_present", c_uint8),
        ("stateless_reset_token", c_uint8 * 16),
    ]


class ngtcp2_version_info(Structure):
    """
    Version info - C layout:
      typedef struct ngtcp2_version_info {
        uint32_t chosen_version;
        const uint8_t *available_versions;
        size_t available_versionslen;
      } ngtcp2_version_info;
    """
    _fields_ = [
        ("chosen_version", c_uint32),
        ("_pad", c_uint32),  # Padding for 64-bit alignment
        ("available_versions", c_void_p),
        ("available_versionslen", c_size_t),
    ]


class ngtcp2_transport_params(Structure):
    """
    ngtcp2_transport_params - Field order must match C struct in ngtcp2.h exactly,
    else conn_new asserts (e.g. active_connection_id_limit >= NGTCP2_DEFAULT_ACTIVE_CONNECTION_ID_LIMIT).
    """
    _fields_ = [
        ("preferred_addr", ngtcp2_preferred_addr),
        ("original_dcid", ngtcp2_cid),
        ("initial_scid", ngtcp2_cid),
        ("retry_scid", ngtcp2_cid),
        ("initial_max_stream_data_bidi_local", c_uint64),
        ("initial_max_stream_data_bidi_remote", c_uint64),
        ("initial_max_stream_data_uni", c_uint64),
        ("initial_max_data", c_uint64),
        ("initial_max_streams_bidi", c_uint64),
        ("initial_max_streams_uni", c_uint64),
        ("max_idle_timeout", c_uint64),
        ("max_udp_payload_size", c_uint64),
        ("active_connection_id_limit", c_uint64),
        ("ack_delay_exponent", c_uint64),
        ("max_ack_delay", c_uint64),
        ("max_datagram_frame_size", c_uint64),
        ("stateless_reset_token_present", c_uint8),
        ("disable_active_migration", c_uint8),
        ("original_dcid_present", c_uint8),
        ("initial_scid_present", c_uint8),
        ("retry_scid_present", c_uint8),
        ("preferred_addr_present", c_uint8),
        # NO padding here - stateless_reset_token follows immediately in C
        ("stateless_reset_token", c_uint8 * 16),
        ("grease_quic_bit", c_uint8),
        # Padding to align version_info to 8-byte boundary (pointer in version_info).
        # Offset after grease_quic_bit = 269; 269 % 8 = 5 -> need 3 bytes to reach 272.
        ("_pad_version_info", c_uint8 * 3),
        ("version_info", ngtcp2_version_info),
        ("version_info_present", c_uint8),
    ]
    
    def __init__(self):
        super().__init__()
        # Will be initialized by ngtcp2_transport_params_default


# Error codes (from ngtcp2.h) - used for logging and handling
NGTCP2_ERR_TRANSPORT_PARAM = -225  # General transport parameter error
NGTCP2_ERR_REQUIRED_TRANSPORT_PARAM = -215
NGTCP2_ERR_MALFORMED_TRANSPORT_PARAM = -216

# Connection error type enum
NGTCP2_CCERR_TYPE_TRANSPORT = 0
NGTCP2_CCERR_TYPE_APPLICATION = 1

class ngtcp2_ccerr(Structure):
    """Connection error structure"""
    _fields_ = [
        ("type", c_uint32),  # ngtcp2_ccerr_type (enum, but use uint32 for ctypes)
        ("error_code", c_uint64),  # Application error code
    ]


# Connection callbacks structure - MUST match C struct exactly (41 function pointers = 328 bytes)
class ngtcp2_conn_callbacks(Structure):
    """
    ngtcp2_callbacks - All callback function pointers in exact C order.
    C struct is 328 bytes (41 x 8 byte pointers on 64-bit).
    """
    _fields_ = [
        # Core callbacks (must be set)
        ("client_initial", c_void_p),
        ("recv_client_initial", c_void_p),
        ("recv_crypto_data", c_void_p),
        ("handshake_completed", c_void_p),
        ("recv_version_negotiation", c_void_p),
        ("encrypt", c_void_p),
        ("decrypt", c_void_p),
        ("hp_mask", c_void_p),
        # Stream callbacks
        ("recv_stream_data", c_void_p),
        ("acked_stream_data_offset", c_void_p),
        ("stream_open", c_void_p),
        ("stream_close", c_void_p),
        ("recv_stateless_reset", c_void_p),
        ("recv_retry", c_void_p),
        ("extend_max_local_streams_bidi", c_void_p),
        ("extend_max_local_streams_uni", c_void_p),
        ("rand", c_void_p),
        ("get_new_connection_id", c_void_p),
        ("remove_connection_id", c_void_p),
        ("update_key", c_void_p),
        ("path_validation", c_void_p),
        ("select_preferred_addr", c_void_p),
        ("stream_reset", c_void_p),
        ("extend_max_remote_streams_bidi", c_void_p),
        ("extend_max_remote_streams_uni", c_void_p),
        ("extend_max_stream_data", c_void_p),
        ("dcid_status", c_void_p),
        ("handshake_confirmed", c_void_p),
        ("recv_new_token", c_void_p),
        ("delete_crypto_aead_ctx", c_void_p),
        ("delete_crypto_cipher_ctx", c_void_p),
        # DATAGRAM support (RFC 9221)
        ("recv_datagram", c_void_p),
        ("ack_datagram", c_void_p),
        ("lost_datagram", c_void_p),
        ("get_path_challenge_data", c_void_p),
        ("stream_stop_sending", c_void_p),
        ("version_negotiation", c_void_p),
        # Key callbacks
        ("recv_rx_key", c_void_p),
        ("recv_tx_key", c_void_p),
        ("tls_early_data_rejected", c_void_p),
        # Added in later versions
        ("begin_path_validation", c_void_p),
    ]


# Crypto connection reference (for TLS integration).
# ABI: get_conn must be first (C code calls it; if user_data were first, C would call conn as a function -> SIGSEGV).
class ngtcp2_crypto_conn_ref(Structure):
    """Crypto connection reference (for TLS integration)"""
    _fields_ = [
        ("get_conn", c_void_p),  # Function pointer: ngtcp2_conn* (*get_conn)(ngtcp2_crypto_conn_ref*)
        ("user_data", c_void_p),
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

# Forward declare functions that will be bound later
ngtcp2_path_storage_init = None
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
        # C API: void ngtcp2_transport_params_default_versioned(int version, ngtcp2_transport_params *params);
        _tp_default_versioned = lib.ngtcp2_transport_params_default_versioned
        _tp_default_versioned.argtypes = [
            c_int,  # transport_params_version (first in C)
            POINTER(ngtcp2_transport_params),
        ]
        _tp_default_versioned.restype = None

        def _transport_params_default_wrapper(params_ptr):
            """Call C library to set defaults (max_udp_payload_size, ack_delay_exponent, max_ack_delay, active_connection_id_limit)."""
            _tp_default_versioned(NGTCP2_TRANSPORT_PARAMS_V1, params_ptr)

        ngtcp2_transport_params_default = _transport_params_default_wrapper
        logger.debug("Using ngtcp2_transport_params_default_versioned")
    except (AttributeError, OSError) as e:
        logger.warning("ngtcp2_transport_params_default_versioned not available: %s, using manual defaults", e)
        # Fallback: same defaults as C (ngtcp2_transport_params.c)
        def _transport_params_default_wrapper(params_ptr):
            ctypes.memset(params_ptr, 0, ctypes.sizeof(ngtcp2_transport_params))
            p = params_ptr.contents if hasattr(params_ptr, 'contents') else None
            if p:
                p.max_udp_payload_size = NGTCP2_DEFAULT_MAX_RECV_UDP_PAYLOAD_SIZE
                p.active_connection_id_limit = NGTCP2_DEFAULT_ACTIVE_CONNECTION_ID_LIMIT
                p.ack_delay_exponent = NGTCP2_DEFAULT_ACK_DELAY_EXPONENT
                p.max_ack_delay = NGTCP2_DEFAULT_MAX_ACK_DELAY
        ngtcp2_transport_params_default = _transport_params_default_wrapper
    
    # Path storage initialization
    try:
        ngtcp2_path_storage_init = lib.ngtcp2_path_storage_init
        ngtcp2_path_storage_init.argtypes = [
            POINTER(ngtcp2_path_storage),  # ps
            c_void_p,  # local_addr (ngtcp2_sockaddr*)
            c_size_t,  # local_addrlen
            c_void_p,  # remote_addr (ngtcp2_sockaddr*)
            c_size_t,  # remote_addrlen
            c_void_p,  # user_data
        ]
        ngtcp2_path_storage_init.restype = None
        logger.debug("ngtcp2_path_storage_init bound successfully")
    except AttributeError:
        ngtcp2_path_storage_init = None
        logger.warning("ngtcp2_path_storage_init function not found")
    
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
        # CRITICAL: ngtcp2 versioned APIs only accept explicit version constants.
        # Passing 0 for any version triggers *len_version(0) -> default -> ngtcp2_unreachable() -> abort.
        # - callbacks_version=0 -> ngtcp2_callbacks.c:70 ngtcp2_callbackslen_version: Unreachable
        # - settings_version=0 -> ngtcp2_settings.c:96 ngtcp2_settingslen_version: Unreachable
        def _conn_server_new(pconn, dcid, scid, path, client_version, callbacks, settings, transport_params, mem, user_data):
            return ngtcp2_conn_server_new_versioned(
                pconn, dcid, scid, path, client_version,
                NGTCP2_CALLBACKS_V1, callbacks,  # 1=valid; 0->Unreachable abort
                NGTCP2_SETTINGS_VERSION, settings,
                NGTCP2_TRANSPORT_PARAMS_VERSION, transport_params,
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
    ngtcp2_conn_read_pkt_versioned_c = None
    ngtcp2_conn_read_pkt_fallback_c = None
    try:
        # Try versioned function (newer API, 7 params)
        _v = lib.ngtcp2_conn_read_pkt_versioned
        _v.argtypes = [
            POINTER(ngtcp2_conn),  # conn
            POINTER(ngtcp2_path),  # path
            c_uint32,  # pkt_info_version (version of ngtcp2_pkt_info, use 0)
            c_void_p,  # ngtcp2_pkt_info *pi (can be NULL)
            POINTER(c_uint8),  # pkt (packet data)
            c_size_t,  # pktlen (packet length)
            c_uint64,  # ts (timestamp in nanoseconds, must be monotonic)
        ]
        _v.restype = c_int
        ngtcp2_conn_read_pkt_versioned_c = _v
        logger.debug("Loaded ngtcp2_conn_read_pkt_versioned")
    except AttributeError:
        pass
    if ngtcp2_conn_read_pkt_versioned_c is None:
        try:
            _f = lib.ngtcp2_conn_read_pkt
            _f.argtypes = [
                POINTER(ngtcp2_conn),
                POINTER(ngtcp2_path),
                c_void_p,  # pi
                POINTER(c_uint8),
                c_size_t,
                c_uint64,
            ]
            _f.restype = c_int
            ngtcp2_conn_read_pkt_fallback_c = _f
            logger.debug("Loaded ngtcp2_conn_read_pkt (fallback)")
        except AttributeError:
            pass

    if ngtcp2_conn_read_pkt_versioned_c is not None or ngtcp2_conn_read_pkt_fallback_c is not None:
        def ngtcp2_conn_read_pkt(conn, path, pkt_info_version, pi, pkt, pktlen, ts):
            """Unified read: 7 args; calls versioned (7 params) or fallback (6 params)."""
            if ngtcp2_conn_read_pkt_versioned_c is not None:
                return ngtcp2_conn_read_pkt_versioned_c(conn, path, pkt_info_version, pi, pkt, pktlen, ts)
            return ngtcp2_conn_read_pkt_fallback_c(conn, path, pi, pkt, pktlen, ts)
    else:
        ngtcp2_conn_read_pkt = None
        logger.warning("ngtcp2_conn_read_pkt function not found")
    
    # Connection management - Write packets (try versioned first)
    # Versioned API: ngtcp2_conn_write_pkt_versioned(conn, path, pkt_info_version, pi, dest, destlen, ts) -> ngtcp2_ssize (no pdatalen in C)
    # Non-versioned: ngtcp2_conn_write_pkt(conn, path, pi, dest, destlen, ts) -> ngtcp2_ssize (bytes written)
    _write_pkt_versioned_c = None
    _write_pkt_fallback_c = None
    try:
        _write_pkt_versioned_c = lib.ngtcp2_conn_write_pkt_versioned
        _write_pkt_versioned_c.argtypes = [
            POINTER(ngtcp2_conn),  # conn
            POINTER(ngtcp2_path),  # path
            c_uint32,  # pkt_info_version (NGTCP2_PKT_INFO_V1)
            c_void_p,  # ngtcp2_pkt_info *pi (can be NULL)
            POINTER(c_uint8),  # dest (output buffer)
            c_size_t,  # destlen (buffer size)
            c_uint64,  # ts (timestamp) - C API has 7 args total; return value is bytes written
        ]
        _write_pkt_versioned_c.restype = c_ssize_t  # ngtcp2_ssize: bytes written or error
        logger.debug("Using ngtcp2_conn_write_pkt_versioned")
    except AttributeError:
        pass
    if _write_pkt_versioned_c is None:
        try:
            _write_pkt_fallback_c = lib.ngtcp2_conn_write_pkt
            _write_pkt_fallback_c.argtypes = [
                POINTER(ngtcp2_conn),
                POINTER(ngtcp2_path),
                c_void_p,  # ngtcp2_pkt_info *pi (can be NULL)
                POINTER(c_uint8),
                c_size_t,
                c_uint64,  # ts
            ]
            _write_pkt_fallback_c.restype = c_ssize_t  # bytes written or error
            logger.debug("Using ngtcp2_conn_write_pkt (fallback)")
        except AttributeError:
            pass

    def ngtcp2_conn_write_pkt(conn, path, dest, destlen, pdatalen, ts):
        """Unified write: (conn, path, dest, destlen, pdatalen, ts). pdatalen is c_size_t byref; ts must be monotonic."""
        if _write_pkt_versioned_c is not None:
            r = _write_pkt_versioned_c(conn, path, NGTCP2_PKT_INFO_V1, None, dest, destlen, ts)
            if r >= 0 and pdatalen is not None:
                try:
                    pdatalen.contents = r
                except Exception:
                    pass
            return r
        if _write_pkt_fallback_c is not None:
            r = _write_pkt_fallback_c(conn, path, None, dest, destlen, ts)
            if r >= 0 and pdatalen is not None:
                try:
                    pdatalen.contents = r
                except Exception:
                    pass
            return r
        return -1
    
    # Connection management - Handle expiry
    # C API: int ngtcp2_conn_handle_expiry(ngtcp2_conn *conn, ngtcp2_tstamp ts);
    try:
        ngtcp2_conn_handle_expiry = lib.ngtcp2_conn_handle_expiry
        ngtcp2_conn_handle_expiry.argtypes = [
            POINTER(ngtcp2_conn),  # conn
            c_uint64,  # ts (timestamp)
        ]
        ngtcp2_conn_handle_expiry.restype = c_int  # 0 on success
    except AttributeError:
        ngtcp2_conn_handle_expiry = None
        logger.warning("ngtcp2_conn_handle_expiry function not found")

    # Must be called after ngtcp2_conn_writev_stream (and similar) so connection state advances for next packet
    # C API: void ngtcp2_conn_update_pkt_tx_time(ngtcp2_conn *conn, ngtcp2_tstamp ts);
    ngtcp2_conn_update_pkt_tx_time = None
    try:
        ngtcp2_conn_update_pkt_tx_time = lib.ngtcp2_conn_update_pkt_tx_time
        ngtcp2_conn_update_pkt_tx_time.argtypes = [POINTER(ngtcp2_conn), c_uint64]
        ngtcp2_conn_update_pkt_tx_time.restype = None
        logger.debug("Loaded ngtcp2_conn_update_pkt_tx_time")
    except AttributeError:
        ngtcp2_conn_update_pkt_tx_time = None
        logger.warning("ngtcp2_conn_update_pkt_tx_time not found")
    
    # Connection management - Close connection
    # Use ngtcp2_conn_write_connection_close_versioned (the actual function name)
    ngtcp2_conn_close = None
    try:
        # Try versioned function first
        ngtcp2_conn_write_connection_close_versioned = lib.ngtcp2_conn_write_connection_close_versioned
        ngtcp2_conn_write_connection_close_versioned.argtypes = [
            POINTER(ngtcp2_conn),  # conn
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
                POINTER(ngtcp2_conn),
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
    
    # Stream management - Submit stream data (queue for send)
    ngtcp2_conn_submit_stream_data = None
    try:
        ngtcp2_conn_submit_stream_data = lib.ngtcp2_conn_submit_stream_data
        ngtcp2_conn_submit_stream_data.argtypes = [
            ngtcp2_conn,  # conn
            c_uint32,  # flags (e.g., NGTCP2_STREAM_DATA_FLAG_FIN)
            c_int64,  # stream_id
            POINTER(c_uint8),  # data
            c_size_t,  # datalen
        ]
        ngtcp2_conn_submit_stream_data.restype = c_int
        logger.debug("Loaded ngtcp2_conn_submit_stream_data")
    except AttributeError:
        ngtcp2_conn_submit_stream_data = None
        logger.debug("ngtcp2_conn_submit_stream_data not available")
    
    # Stream management - Write stream data
    # Use ngtcp2_conn_writev_stream_versioned (actual exported symbol)
    ngtcp2_conn_writev_stream_versioned = None
    ngtcp2_conn_writev_stream = None
    ngtcp2_strm_write = None
    try:
        # Versioned function signature (ngtcp2.h)
        ngtcp2_conn_writev_stream_versioned = lib.ngtcp2_conn_writev_stream_versioned
        ngtcp2_conn_writev_stream_versioned.argtypes = [
            ngtcp2_conn,  # conn
            POINTER(ngtcp2_path),  # path
            c_int,  # pkt_info_version
            POINTER(ngtcp2_pkt_info),  # pi (can be NULL)
            POINTER(c_uint8),  # dest
            c_size_t,  # destlen
            POINTER(c_ssize_t),  # pdatalen (bytes of stream data consumed)
            c_uint32,  # flags
            c_int64,  # stream_id
            POINTER(ngtcp2_vec),  # datav
            c_size_t,  # datavcnt
            c_uint64,  # ts (timestamp)
        ]
        ngtcp2_conn_writev_stream_versioned.restype = c_ssize_t  # bytes written or error
        logger.debug("Using ngtcp2_conn_writev_stream_versioned")
    except AttributeError:
        ngtcp2_conn_writev_stream_versioned = None
        logger.warning("ngtcp2_conn_writev_stream_versioned not found")
    
    # Stream management - Shutdown stream
    # Use ngtcp2_conn_shutdown_stream (the actual function name)
    try:
        ngtcp2_strm_shutdown = lib.ngtcp2_conn_shutdown_stream
        ngtcp2_strm_shutdown.argtypes = [
            POINTER(ngtcp2_conn),  # conn
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
    
    # Connection keep-alive
    try:
        ngtcp2_conn_set_keep_alive_timeout = lib.ngtcp2_conn_set_keep_alive_timeout
        ngtcp2_conn_set_keep_alive_timeout.argtypes = [POINTER(ngtcp2_conn), c_uint64]
        ngtcp2_conn_set_keep_alive_timeout.restype = None
    except AttributeError:
        ngtcp2_conn_set_keep_alive_timeout = None
    
    try:
        ngtcp2_conn_get_remote_transport_params = lib.ngtcp2_conn_get_remote_transport_params
        ngtcp2_conn_get_remote_transport_params.argtypes = [POINTER(ngtcp2_conn)]
        ngtcp2_conn_get_remote_transport_params.restype = POINTER(ngtcp2_transport_params)
    except AttributeError:
        ngtcp2_conn_get_remote_transport_params = None
    
    # Get current path from connection
    try:
        ngtcp2_conn_get_path = lib.ngtcp2_conn_get_path
        ngtcp2_conn_get_path.argtypes = [POINTER(ngtcp2_conn)]
        ngtcp2_conn_get_path.restype = POINTER(ngtcp2_path)
    except AttributeError:
        ngtcp2_conn_get_path = None
    
    # Load wrapper library for amplification limit workaround
    ngtcp2_conn_force_validate_path = None
    ngtcp2_path_is_local_network = None
    try:
        import os
        wrapper_path = os.path.join(os.path.dirname(__file__), "libngtcp2_wrapper.so")
        if os.path.exists(wrapper_path):
            wrapper_lib = ctypes.CDLL(wrapper_path)
            ngtcp2_conn_force_validate_path = wrapper_lib.ngtcp2_conn_force_validate_path
            ngtcp2_conn_force_validate_path.argtypes = [POINTER(ngtcp2_conn)]
            ngtcp2_conn_force_validate_path.restype = c_int
            
            ngtcp2_path_is_local_network = wrapper_lib.ngtcp2_path_is_local_network
            ngtcp2_path_is_local_network.argtypes = [POINTER(ngtcp2_path)]
            ngtcp2_path_is_local_network.restype = c_int
            logger.info("Loaded ngtcp2_wrapper library (amplification limit workaround)")
    except Exception as e:
        logger.debug(f"ngtcp2_wrapper not available: {e}")
    
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
        ngtcp2_conn_get_expiry.argtypes = [POINTER(ngtcp2_conn)]
        ngtcp2_conn_get_expiry.restype = c_uint64  # ngtcp2_tstamp
    except AttributeError:
        ngtcp2_conn_get_expiry = None

    try:
        ngtcp2_conn_get_timestamp = lib.ngtcp2_conn_get_timestamp
        ngtcp2_conn_get_timestamp.argtypes = [POINTER(ngtcp2_conn)]
        ngtcp2_conn_get_timestamp.restype = c_uint64  # ngtcp2_tstamp (conn->log.last_ts)
    except AttributeError:
        ngtcp2_conn_get_timestamp = None
    
    try:
        ngtcp2_conn_get_handshake_completed = lib.ngtcp2_conn_get_handshake_completed
        ngtcp2_conn_get_handshake_completed.argtypes = [POINTER(ngtcp2_conn)]
        ngtcp2_conn_get_handshake_completed.restype = c_int  # boolean
    except AttributeError:
        ngtcp2_conn_get_handshake_completed = None
    
    try:
        ngtcp2_conn_set_tls_native_handle = lib.ngtcp2_conn_set_tls_native_handle
        ngtcp2_conn_set_tls_native_handle.argtypes = [POINTER(ngtcp2_conn), c_void_p]  # conn*, tls_native_handle (SSL*)
        ngtcp2_conn_set_tls_native_handle.restype = None
    except AttributeError:
        ngtcp2_conn_set_tls_native_handle = None
    
    try:
        ngtcp2_conn_del = lib.ngtcp2_conn_del
        ngtcp2_conn_del.argtypes = [POINTER(ngtcp2_conn), c_void_p]  # conn*, mem (can be NULL)
        ngtcp2_conn_del.restype = None
    except AttributeError:
        ngtcp2_conn_del = None

else:
    # Set all to None if library not available
    ngtcp2_version = None
    ngtcp2_settings_default = None
    ngtcp2_transport_params_default = None
    ngtcp2_path_storage_init = None
    ngtcp2_conn_server_new = None
    ngtcp2_accept = None
    ngtcp2_conn_read_pkt = None
    ngtcp2_conn_write_pkt = None
    ngtcp2_conn_handle_expiry = None
    ngtcp2_conn_update_pkt_tx_time = None
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
    ngtcp2_conn_get_timestamp = None
    ngtcp2_conn_get_handshake_completed = None
    ngtcp2_conn_set_tls_native_handle = None
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
