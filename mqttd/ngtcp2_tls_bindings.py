"""
ngtcp2 TLS Integration Bindings - Phase 2 Implementation
Based on curl's curl_ngtcp2.c and vquic-tls.c reference implementation

This module provides Python ctypes bindings for TLS (protocol) integration with ngtcp2.
OpenSSL 3.x implements TLS 1.3; our Python API uses "TLS" naming (tls_ctx, tls_session).
The underlying C API still uses historical "SSL" names (SSL_CTX_new, SSL_new, etc.).

Supports OpenSSL 3.x and wolfSSL.

Reference:
- curl/lib/vquic/curl_ngtcp2.c
- curl/lib/vquic/vquic-tls.c
- ngtcp2 crypto API: https://nghttp2.org/ngtcp2/
"""

import ctypes
from ctypes import (
    CDLL, Structure, POINTER, CFUNCTYPE, byref,
    c_int, c_int32, c_int64, c_uint, c_uint8, c_uint16, c_uint32, c_uint64, c_ubyte,
    c_size_t, c_ssize_t, c_void_p, c_char_p, c_bool,
    Array, cast
)
import logging
import os
from typing import Optional, Callable, Tuple, Any
import sys

logger = logging.getLogger(__name__)

# Import ngtcp2 bindings
# CRITICAL: Import bindings first to ensure NGTCP2_AVAILABLE is set
try:
    from .ngtcp2_bindings import (
        ngtcp2_conn, ngtcp2_cid, NGTCP2_AVAILABLE,
        NGTCP2_MILLISECONDS, NGTCP2_SECONDS, NGTCP2_MICROSECONDS,
    )
    # NGTCP2_AVAILABLE is imported from bindings
except ImportError:
    # Fallback if importing as module
    try:
        from mqttd.ngtcp2_bindings import (
            ngtcp2_conn, ngtcp2_cid, NGTCP2_AVAILABLE,
            NGTCP2_MILLISECONDS, NGTCP2_SECONDS, NGTCP2_MICROSECONDS,
        )
    except ImportError:
        logger.warning("ngtcp2_bindings not available")
        NGTCP2_AVAILABLE = False
        ngtcp2_conn = c_void_p
        ngtcp2_cid = None


# Constants
USE_OPENSSL = False
USE_WOLFSSL = False

# TLS library handles
_openssl_lib = None
_wolfssl_lib = None
_ngtcp2_crypto_lib = None

OPENSSL_AVAILABLE = False
WOLFSSL_AVAILABLE = False
NGTCP2_CRYPTO_AVAILABLE = False


def _load_openssl_library():
    """Load OpenSSL library"""
    global _openssl_lib, OPENSSL_AVAILABLE
    
    if _openssl_lib is not None:
        return OPENSSL_AVAILABLE
    
    lib_names = [
        'libssl.so',
        'libssl.so.3',
        'libssl.so.1.1',
        'ssl',
    ]
    
    for lib_name in lib_names:
        try:
            _openssl_lib = CDLL(lib_name)
            # Test a simple function to verify it's OpenSSL
            # Note: SSL_library_init doesn't exist in OpenSSL 3.x, use TLS_server_method instead
            try:
                _openssl_lib.TLS_server_method
                OPENSSL_AVAILABLE = True
                logger.info(f"Loaded OpenSSL library: {lib_name}")
                return True
            except AttributeError:
                _openssl_lib = None
                continue
        except OSError:
            continue
    
    OPENSSL_AVAILABLE = False
    return False


def _load_wolfssl_library():
    """Load wolfSSL library"""
    global _wolfssl_lib, WOLFSSL_AVAILABLE
    
    if _wolfssl_lib is not None:
        return WOLFSSL_AVAILABLE
    
    lib_names = [
        'libwolfssl.so',
        'libwolfssl.so.0',
        'wolfssl',
    ]
    
    for lib_name in lib_names:
        try:
            _wolfssl_lib = CDLL(lib_name)
            # Test a simple function to verify it's wolfSSL
            try:
                _wolfssl_lib.wolfSSL_Init
                WOLFSSL_AVAILABLE = True
                logger.info(f"Loaded wolfSSL library: {lib_name}")
                return True
            except AttributeError:
                _wolfssl_lib = None
                continue
        except OSError:
            continue
    
    WOLFSSL_AVAILABLE = False
    return False


def _local_lib_dirs():
    """Return ($HOME/.local/lib64, $HOME/.local/lib) using runtime HOME. Prefer $HOME/.local for builds with --prefix $HOME/.local."""
    home = os.environ.get("HOME") or os.path.expanduser("~")
    return (os.path.join(home, ".local", "lib64"), os.path.join(home, ".local", "lib"))


def _prepend_ld_library_path():
    """Prepend $HOME/.local then /usr/local to LD_LIBRARY_PATH so the loader finds
    libngtcp2_crypto_wolfssl and its dependencies. Uses runtime HOME.
    Only affects the current process; safe to call multiple times (idempotent for first call).
    """
    key = "LD_LIBRARY_PATH"
    local64, local_lib = _local_lib_dirs()
    parts = [d for d in (local64, local_lib) if os.path.isdir(d)]
    parts.extend(["/usr/local/lib64", "/usr/local/lib"])
    prefix = ":".join(parts)
    current = os.environ.get(key, "")
    first = parts[0] if parts else ""
    if not (first and current.startswith(first)):
        os.environ[key] = f"{prefix}{':' + current if current else ''}"


def _load_ngtcp2_crypto_library():
    """Load ngtcp2 crypto library (OpenSSL or wolfSSL backend)"""
    global _ngtcp2_crypto_lib, NGTCP2_CRYPTO_AVAILABLE, USE_OPENSSL, USE_WOLFSSL
    
    if _ngtcp2_crypto_lib is not None:
        return NGTCP2_CRYPTO_AVAILABLE
    
    # Prefer $HOME/.local (build-server.sh --prefix $HOME/.local); set LD_LIBRARY_PATH first
    _prepend_ld_library_path()
    local64, local_lib = _local_lib_dirs()

    # Try to determine which TLS backend ngtcp2 was built with.
    # Prefer WolfSSL first so server matches client (capacitor-mqtt-quic uses WolfSSL by default).
    lib_names = [
        'libngtcp2_crypto_wolfssl.so',
        'libngtcp2_crypto_wolfssl.so.0',
        'libngtcp2_crypto_ossl.so',
        'libngtcp2_crypto_ossl.so.0',
        'libngtcp2_crypto_ossl.so.0.1.1',
        'libngtcp2_crypto_quictls.so',
        'libngtcp2_crypto_quictls.so.0',
    ]

    # Filesystem-driven path order: only try dirs that contain the library, $HOME/.local first.
    # Preferred order: $HOME/.local/lib64, $HOME/.local/lib, /usr/local/lib64, /usr/local/lib.
    # RHEL puts .so in lib64; checking existence avoids trying /usr/local when lib is in .local.
    _WOLFSSL_BASES = (
        "libngtcp2_crypto_wolfssl.so",
        "libngtcp2_crypto_wolfssl.so.5",
        "libngtcp2_crypto_wolfssl.so.5.3.1",
    )
    _PREFERRED_DIRS = (
        local64,
        local_lib,
        "/usr/local/lib64",
        "/usr/local/lib",
    )

    full_paths = []
    for _dir in _PREFERRED_DIRS:
        if not os.path.isdir(_dir):
            continue
        if any(os.path.lexists(os.path.join(_dir, b)) for b in _WOLFSSL_BASES):
            full_paths.extend(os.path.join(_dir, b) for b in _WOLFSSL_BASES)
            break
    if not full_paths:
        full_paths = [os.path.join(d, b) for d in _PREFERRED_DIRS for b in _WOLFSSL_BASES if os.path.isdir(d)]

    # OpenSSL fallbacks: same order ($HOME/.local/lib64, .local/lib, /usr/local)
    _OSSL_PATHS = [
        os.path.join(local64, "libngtcp2_crypto_ossl.so"),
        os.path.join(local_lib, "libngtcp2_crypto_ossl.so"),
        "/usr/local/lib64/libngtcp2_crypto_ossl.so",
        "/usr/local/lib64/libngtcp2_crypto_ossl.so.0",
        "/usr/local/lib/libngtcp2_crypto_ossl.so",
        "/usr/local/lib/libngtcp2_crypto_ossl.so.0",
        "/usr/local/lib/libngtcp2_crypto_ossl.so.0.1.1",
    ]
    full_paths.extend(_OSSL_PATHS)
    require_wolfssl = os.environ.get("MQTTD_QUIC_USE_WOLFSSL", "").strip().lower() in ("1", "yes", "true")
    all_lib_names = full_paths + lib_names
    wolfssl_tried = False
    first_wolfssl_load_error = None

    for lib_name in all_lib_names:
        try:
            _ngtcp2_crypto_lib = CDLL(lib_name)
            # Try to find initialization function
            try:
                # Check for OpenSSL backend
                if 'ossl' in lib_name or 'quictls' in lib_name:
                    if require_wolfssl:
                        _ngtcp2_crypto_lib = None
                        continue
                    _ngtcp2_crypto_lib.ngtcp2_crypto_ossl_init
                    NGTCP2_CRYPTO_AVAILABLE = True
                    USE_OPENSSL = True  # Set USE_OPENSSL when we find ossl library
                    if wolfssl_tried:
                        logger.info(
                            "Loaded ngtcp2 crypto (OpenSSL) library: %s (WolfSSL not found; "
                            "build server with scripts/build-wolfssl-ngtcp2.sh and set LD_LIBRARY_PATH=/usr/local/lib64 or /usr/local/lib to match client)",
                            lib_name,
                        )
                    else:
                        logger.info(f"Loaded ngtcp2 crypto (OpenSSL) library: {lib_name}")
                    return True
                elif 'wolfssl' in lib_name:
                    # Newer ngtcp2 may not export ngtcp2_crypto_wolfssl_init; detect by any wolfssl symbol
                    if getattr(_ngtcp2_crypto_lib, 'ngtcp2_crypto_wolfssl_configure_server_context', None) is None:
                        raise AttributeError("wolfssl backend missing required symbol")
                    NGTCP2_CRYPTO_AVAILABLE = True
                    USE_WOLFSSL = True  # Set USE_WOLFSSL when we find wolfssl library
                    logger.info(f"Loaded ngtcp2 crypto (wolfSSL) library: {lib_name}")
                    return True
            except AttributeError:
                _ngtcp2_crypto_lib = None
                continue
        except OSError as e:
            if 'wolfssl' in lib_name:
                wolfssl_tried = True
                if first_wolfssl_load_error is None:
                    first_wolfssl_load_error = (lib_name, e)
            continue

    if require_wolfssl:
        if first_wolfssl_load_error:
            logger.error(
                "Load failed for %s: %s",
                first_wolfssl_load_error[0],
                first_wolfssl_load_error[1],
            )
        logger.error(
            "MQTTD_QUIC_USE_WOLFSSL=1 but libngtcp2_crypto_wolfssl not found. "
            "Run scripts/build-wolfssl-ngtcp2.sh (from mqttd root). "
            "If installed under a prefix, set before running: "
            "export LD_LIBRARY_PATH=$HOME/.local/lib64:$HOME/.local/lib  (or /usr/local/lib64:/usr/local/lib)"
        )
    elif wolfssl_tried:
        logger.warning(
            "libngtcp2_crypto_wolfssl not found; using OpenSSL. "
            "To match capacitor-mqtt-quic client: run scripts/build-wolfssl-ngtcp2.sh on this server, "
            "then LD_LIBRARY_PATH=/usr/local/lib64 (or /usr/local/lib) when starting the server."
        )
    NGTCP2_CRYPTO_AVAILABLE = False
    logger.warning("ngtcp2 crypto library not found")
    return False


# Load libraries on import
# CRITICAL: Initialize TLS backend immediately when module loads
# ngtcp2 requires TLS backend to be initialized before ANY ngtcp2 function calls
if NGTCP2_AVAILABLE:
    _load_openssl_library()
    _load_wolfssl_library()
    _load_ngtcp2_crypto_library()
    
    if OPENSSL_AVAILABLE and not USE_OPENSSL:
        USE_OPENSSL = True
    elif WOLFSSL_AVAILABLE and not USE_WOLFSSL:
        USE_WOLFSSL = True
    
    # Bind crypto functions now that libraries are loaded
    _bind_crypto_functions()
    
    # Initialize TLS backend immediately (before any ngtcp2 calls)
    # This is critical - ngtcp2 will crash without TLS backend
    try:
        if init_tls_backend():
            logger.info("TLS backend initialized successfully on module import")
        else:
            logger.debug("TLS backend initialization deferred (will retry on first use)")
    except Exception as e:
        logger.debug(f"TLS backend initialization deferred: {e}")


# OpenSSL Core API and QUIC API
SSL_CTX_new = None
SSL_CTX_free = None
SSL_CTX_use_certificate_file = None
SSL_CTX_use_PrivateKey_file = None
SSL_CTX_set_min_proto_version = None
SSL_CTX_set_alpn_select_cb = None
SSL_new = None
SSL_free = None
SSL_set_accept_state = None
SSL_set_connect_state = None
SSL_set_app_data = None
SSL_get_app_data = None
TLS_server_method = None
ngtcp2_crypto_ossl_configure_server_session = None
ngtcp2_crypto_ossl_ctx_new = None
ngtcp2_crypto_ossl_ctx_set_ssl = None
ngtcp2_crypto_ossl_ctx_get_ssl = None
ngtcp2_crypto_ossl_ctx_del = None
ngtcp2_conn_set_tls_native_handle = None

# SSL_FILETYPE constants
SSL_FILETYPE_PEM = 1
SSL_FILETYPE_ASN1 = 2

# TLS version constants
TLS1_3_VERSION = 0x0304

if USE_OPENSSL and OPENSSL_AVAILABLE and _openssl_lib:
    ssl_lib = _openssl_lib
    
    # SSL context types (simplified)
    SSL_CTX = c_void_p
    SSL = c_void_p
    
    # Core SSL functions
    try:
        TLS_server_method = ssl_lib.TLS_server_method
        TLS_server_method.argtypes = []
        TLS_server_method.restype = c_void_p
    except AttributeError:
        TLS_server_method = None
    
    try:
        SSL_CTX_new = ssl_lib.SSL_CTX_new
        SSL_CTX_new.argtypes = [c_void_p]  # method
        SSL_CTX_new.restype = c_void_p
    except AttributeError:
        SSL_CTX_new = None
    
    try:
        SSL_CTX_free = ssl_lib.SSL_CTX_free
        SSL_CTX_free.argtypes = [c_void_p]
        SSL_CTX_free.restype = None
    except AttributeError:
        SSL_CTX_free = None
    
    try:
        SSL_CTX_use_certificate_file = ssl_lib.SSL_CTX_use_certificate_file
        SSL_CTX_use_certificate_file.argtypes = [c_void_p, c_char_p, c_int]
        SSL_CTX_use_certificate_file.restype = c_int
    except AttributeError:
        SSL_CTX_use_certificate_file = None
    
    try:
        SSL_CTX_use_PrivateKey_file = ssl_lib.SSL_CTX_use_PrivateKey_file
        SSL_CTX_use_PrivateKey_file.argtypes = [c_void_p, c_char_p, c_int]
        SSL_CTX_use_PrivateKey_file.restype = c_int
    except AttributeError:
        SSL_CTX_use_PrivateKey_file = None
    
    try:
        SSL_CTX_set_min_proto_version = ssl_lib.SSL_CTX_set_min_proto_version
        SSL_CTX_set_min_proto_version.argtypes = [c_void_p, c_int]
        SSL_CTX_set_min_proto_version.restype = c_int
    except AttributeError:
        SSL_CTX_set_min_proto_version = None
    
    try:
        SSL_new = ssl_lib.SSL_new
        SSL_new.argtypes = [c_void_p]  # ctx
        SSL_new.restype = c_void_p
    except AttributeError:
        SSL_new = None
    
    try:
        SSL_free = ssl_lib.SSL_free
        SSL_free.argtypes = [c_void_p]
        SSL_free.restype = None
    except AttributeError:
        SSL_free = None
    
    try:
        SSL_set_accept_state = ssl_lib.SSL_set_accept_state
        SSL_set_accept_state.argtypes = [c_void_p]
        SSL_set_accept_state.restype = None
    except AttributeError:
        SSL_set_accept_state = None
    
    # QUIC API functions (OpenSSL 3.2+)
    try:
        SSL_set_quic_method = ssl_lib.SSL_set_quic_method
        SSL_set_quic_method.argtypes = [
            SSL,  # ssl
            c_void_p,  # quic_method (OPAQUE pointer)
        ]
        SSL_set_quic_method.restype = c_int
    except AttributeError:
        SSL_set_quic_method = None
        logger.warning("OpenSSL QUIC API not available (requires OpenSSL 3.2+)")
    
    try:
        SSL_provide_quic_data = ssl_lib.SSL_provide_quic_data
        SSL_provide_quic_data.argtypes = [
            SSL,  # ssl
            c_uint32,  # level (SSL_QUIC_DATA_LEVEL_*)
            POINTER(c_uint8),  # data
            c_size_t,  # len
        ]
        SSL_provide_quic_data.restype = c_int
    except AttributeError:
        SSL_provide_quic_data = None
    
    try:
        SSL_process_quic_post_handshake = ssl_lib.SSL_process_quic_post_handshake
        SSL_process_quic_post_handshake.argtypes = [SSL]  # ssl
        SSL_process_quic_post_handshake.restype = c_int
    except AttributeError:
        SSL_process_quic_post_handshake = None
    
    try:
        SSL_read_quic = ssl_lib.SSL_read_quic
        SSL_read_quic.argtypes = [
            SSL,  # ssl
            POINTER(c_uint8),  # buf
            c_size_t,  # len
            POINTER(c_size_t),  # readbytes (out)
            c_uint64,  # offset
        ]
        SSL_read_quic.restype = c_int
    except AttributeError:
        SSL_read_quic = None
    
    try:
        SSL_write_quic = ssl_lib.SSL_write_quic
        SSL_write_quic.argtypes = [
            SSL,  # ssl
            POINTER(c_uint8),  # buf
            c_size_t,  # len
            POINTER(c_size_t),  # writtenbytes (out)
            c_uint64,  # offset
        ]
        SSL_write_quic.restype = c_int
    except AttributeError:
        SSL_write_quic = None
    
    # QUIC data levels
    SSL_QUIC_DATA_LEVEL_INITIAL = 0
    SSL_QUIC_DATA_LEVEL_HANDSHAKE = 1
    SSL_QUIC_DATA_LEVEL_0RTT = 2
    SSL_QUIC_DATA_LEVEL_1RTT = 3
    
else:
    SSL_set_quic_method = None
    SSL_provide_quic_data = None
    SSL_process_quic_post_handshake = None
    SSL_read_quic = None
    SSL_write_quic = None
    SSL_QUIC_DATA_LEVEL_INITIAL = 0
    SSL_QUIC_DATA_LEVEL_HANDSHAKE = 1
    SSL_QUIC_DATA_LEVEL_0RTT = 2
    SSL_QUIC_DATA_LEVEL_1RTT = 3


# wolfSSL QUIC API
if USE_WOLFSSL and WOLFSSL_AVAILABLE and _wolfssl_lib:
    wolfssl_lib = _wolfssl_lib
    
    # wolfSSL context types (simplified)
    WOLFSSL_CTX = c_void_p
    WOLFSSL = c_void_p
    
    # QUIC API functions
    try:
        wolfSSL_set_quic_method = wolfssl_lib.wolfSSL_set_quic_method
        wolfSSL_set_quic_method.argtypes = [
            WOLFSSL,  # ssl
            c_void_p,  # quic_method
        ]
        wolfSSL_set_quic_method.restype = c_int
    except AttributeError:
        wolfSSL_set_quic_method = None
        logger.warning("wolfSSL QUIC API not available")
    
    try:
        wolfSSL_provide_quic_data = wolfssl_lib.wolfSSL_provide_quic_data
        wolfSSL_provide_quic_data.argtypes = [
            WOLFSSL,  # ssl
            c_uint32,  # level
            POINTER(c_uint8),  # data
            c_size_t,  # len
        ]
        wolfSSL_provide_quic_data.restype = c_int
    except AttributeError:
        wolfSSL_provide_quic_data = None
    
    # Similar functions as OpenSSL...
    
else:
    wolfSSL_set_quic_method = None
    wolfSSL_provide_quic_data = None


# ngtcp2 crypto integration functions
# Initialize to None first (will be set by _bind_crypto_functions)
ngtcp2_crypto_ossl_init = None
ngtcp2_crypto_wolfssl_init = None

# Crypto callback function pointers (from libngtcp2_crypto_ossl)
# These are pre-built callbacks that handle encryption/decryption
ngtcp2_crypto_recv_client_initial_cb = None
ngtcp2_crypto_recv_crypto_data_cb = None
ngtcp2_crypto_encrypt_cb = None
ngtcp2_crypto_decrypt_cb = None
ngtcp2_crypto_hp_mask_cb = None
ngtcp2_crypto_update_key_cb = None
ngtcp2_crypto_delete_crypto_aead_ctx_cb = None
ngtcp2_crypto_delete_crypto_cipher_ctx_cb = None
ngtcp2_crypto_get_path_challenge_data_cb = None

# Bind crypto functions - this runs after libraries are loaded
def _bind_crypto_functions():
    """Bind ngtcp2 crypto functions after library is loaded"""
    # Declare ALL module-level globals that we need to modify
    global ngtcp2_crypto_ossl_init, ngtcp2_crypto_wolfssl_init
    global ngtcp2_crypto_recv_client_initial_cb, ngtcp2_crypto_recv_crypto_data_cb
    global ngtcp2_crypto_encrypt_cb, ngtcp2_crypto_decrypt_cb, ngtcp2_crypto_hp_mask_cb
    global ngtcp2_crypto_update_key_cb
    global ngtcp2_crypto_delete_crypto_aead_ctx_cb, ngtcp2_crypto_delete_crypto_cipher_ctx_cb
    global ngtcp2_crypto_get_path_challenge_data_cb
    
    if NGTCP2_CRYPTO_AVAILABLE and _ngtcp2_crypto_lib:
        crypto_lib = _ngtcp2_crypto_lib
        
        # OpenSSL backend initialization
        try:
            # Use getattr to safely get the function
            func = getattr(crypto_lib, 'ngtcp2_crypto_ossl_init', None)
            if func is not None:
                ngtcp2_crypto_ossl_init = func
                ngtcp2_crypto_ossl_init.argtypes = []
                ngtcp2_crypto_ossl_init.restype = c_int  # 0 on success
                logger.info("Bound ngtcp2_crypto_ossl_init")
        except Exception as e:
            logger.debug(f"Error binding ngtcp2_crypto_ossl_init: {e}")
        
        # wolfSSL backend initialization
        try:
            func = getattr(crypto_lib, 'ngtcp2_crypto_wolfssl_init', None)
            if func is not None:
                ngtcp2_crypto_wolfssl_init = func
                ngtcp2_crypto_wolfssl_init.argtypes = []
                ngtcp2_crypto_wolfssl_init.restype = c_int
                logger.info("Bound ngtcp2_crypto_wolfssl_init")
        except Exception as e:
            logger.debug(f"Error binding ngtcp2_crypto_wolfssl_init: {e}")
        
        # Bind crypto callback function pointers
        # These are function addresses that can be used directly in ngtcp2_callbacks
        try:
            func = getattr(crypto_lib, 'ngtcp2_crypto_recv_client_initial_cb', None)
            if func:
                ngtcp2_crypto_recv_client_initial_cb = ctypes.cast(func, c_void_p).value
                logger.debug(f"Bound ngtcp2_crypto_recv_client_initial_cb: {hex(ngtcp2_crypto_recv_client_initial_cb)}")
        except Exception as e:
            logger.debug(f"Error binding recv_client_initial_cb: {e}")
        
        try:
            func = getattr(crypto_lib, 'ngtcp2_crypto_recv_crypto_data_cb', None)
            if func:
                ngtcp2_crypto_recv_crypto_data_cb = ctypes.cast(func, c_void_p).value
                logger.debug(f"Bound ngtcp2_crypto_recv_crypto_data_cb: {hex(ngtcp2_crypto_recv_crypto_data_cb)}")
        except Exception as e:
            logger.debug(f"Error binding recv_crypto_data_cb: {e}")
        
        try:
            func = getattr(crypto_lib, 'ngtcp2_crypto_encrypt_cb', None)
            if func:
                ngtcp2_crypto_encrypt_cb = ctypes.cast(func, c_void_p).value
                logger.debug(f"Bound ngtcp2_crypto_encrypt_cb: {hex(ngtcp2_crypto_encrypt_cb)}")
        except Exception as e:
            logger.debug(f"Error binding encrypt_cb: {e}")
        
        try:
            func = getattr(crypto_lib, 'ngtcp2_crypto_decrypt_cb', None)
            if func:
                ngtcp2_crypto_decrypt_cb = ctypes.cast(func, c_void_p).value
                logger.debug(f"Bound ngtcp2_crypto_decrypt_cb: {hex(ngtcp2_crypto_decrypt_cb)}")
        except Exception as e:
            logger.debug(f"Error binding decrypt_cb: {e}")
        
        try:
            func = getattr(crypto_lib, 'ngtcp2_crypto_hp_mask_cb', None)
            if func:
                ngtcp2_crypto_hp_mask_cb = ctypes.cast(func, c_void_p).value
                logger.debug(f"Bound ngtcp2_crypto_hp_mask_cb: {hex(ngtcp2_crypto_hp_mask_cb)}")
        except Exception as e:
            logger.debug(f"Error binding hp_mask_cb: {e}")
        
        try:
            func = getattr(crypto_lib, 'ngtcp2_crypto_update_key_cb', None)
            if func:
                ngtcp2_crypto_update_key_cb = ctypes.cast(func, c_void_p).value
                logger.debug(f"Bound ngtcp2_crypto_update_key_cb: {hex(ngtcp2_crypto_update_key_cb)}")
        except Exception as e:
            logger.debug(f"Error binding update_key_cb: {e}")
        
        try:
            func = getattr(crypto_lib, 'ngtcp2_crypto_delete_crypto_aead_ctx_cb', None)
            if func:
                ngtcp2_crypto_delete_crypto_aead_ctx_cb = ctypes.cast(func, c_void_p).value
                logger.debug(f"Bound ngtcp2_crypto_delete_crypto_aead_ctx_cb: {hex(ngtcp2_crypto_delete_crypto_aead_ctx_cb)}")
        except Exception as e:
            logger.debug(f"Error binding delete_crypto_aead_ctx_cb: {e}")
        
        try:
            func = getattr(crypto_lib, 'ngtcp2_crypto_delete_crypto_cipher_ctx_cb', None)
            if func:
                ngtcp2_crypto_delete_crypto_cipher_ctx_cb = ctypes.cast(func, c_void_p).value
                logger.debug(f"Bound ngtcp2_crypto_delete_crypto_cipher_ctx_cb: {hex(ngtcp2_crypto_delete_crypto_cipher_ctx_cb)}")
        except Exception as e:
            logger.debug(f"Error binding delete_crypto_cipher_ctx_cb: {e}")
        
        try:
            func = getattr(crypto_lib, 'ngtcp2_crypto_get_path_challenge_data_cb', None)
            if func:
                ngtcp2_crypto_get_path_challenge_data_cb = ctypes.cast(func, c_void_p).value
                logger.debug(f"Bound ngtcp2_crypto_get_path_challenge_data_cb: {hex(ngtcp2_crypto_get_path_challenge_data_cb)}")
        except Exception as e:
            logger.debug(f"Error binding get_path_challenge_data_cb: {e}")
        
        # Bind ngtcp2_crypto_ossl_configure_server_session
        try:
            func = getattr(crypto_lib, 'ngtcp2_crypto_ossl_configure_server_session', None)
            if func:
                ngtcp2_crypto_ossl_configure_server_session = func
                ngtcp2_crypto_ossl_configure_server_session.argtypes = [c_void_p]  # SSL*
                ngtcp2_crypto_ossl_configure_server_session.restype = c_int
                logger.debug("Bound ngtcp2_crypto_ossl_configure_server_session")
        except Exception as e:
            logger.debug(f"Error binding configure_server_session: {e}")
        # Bind ossl_ctx API (tls_native_handle must be crypto_ossl_ctx*, not raw SSL*)
        # ngtcp2_crypto_ossl_ctx_new(possl_ctx, ssl) writes new ctx to *possl_ctx; returns 0 on success
        try:
            for name, (argtypes, restype) in [
                ('ngtcp2_crypto_ossl_ctx_new', ([POINTER(c_void_p), c_void_p], c_int)),  # possl_ctx (out), ssl
                ('ngtcp2_crypto_ossl_ctx_set_ssl', ([c_void_p, c_void_p], None)),  # ctx, ssl
                ('ngtcp2_crypto_ossl_ctx_get_ssl', ([c_void_p], c_void_p)),
                ('ngtcp2_crypto_ossl_ctx_del', ([c_void_p], None)),
            ]:
                func = getattr(crypto_lib, name, None)
                if func:
                    func.argtypes = argtypes
                    func.restype = restype
                    globals()[name] = func
                    logger.debug(f"Bound {name}")
        except Exception as e:
            logger.debug(f"Error binding ossl_ctx API: {e}")
        
        return ngtcp2_crypto_ossl_init is not None or ngtcp2_crypto_wolfssl_init is not None
    
    return False

# Load libraries and bind functions on import
if NGTCP2_AVAILABLE:
    _load_openssl_library()
    _load_wolfssl_library()
    _load_ngtcp2_crypto_library()
    
    if OPENSSL_AVAILABLE and not USE_OPENSSL:
        USE_OPENSSL = True
    elif WOLFSSL_AVAILABLE and not USE_WOLFSSL:
        USE_WOLFSSL = True
    
    # Bind crypto functions now that libraries are loaded
    _bind_crypto_functions()
    
    # Initialize TLS backend immediately (before any ngtcp2 calls)
    # This is critical - ngtcp2 will crash without TLS backend
    try:
        if init_tls_backend():
            logger.info("TLS backend initialized successfully on module import")
        else:
            logger.warning("TLS backend initialization failed on module import - will retry on first connection")
    except Exception as e:
        logger.debug(f"TLS backend initialization deferred: {e}")
    
    # TLS callback types
    # These are typically provided by ngtcp2 crypto backend
    
else:
    ngtcp2_crypto_ossl_init = None
    ngtcp2_crypto_wolfssl_init = None


# TLS handshake callback type
TLSHandshakeFunc = CFUNCTYPE(
    c_int,  # return: 0 on success
    c_void_p,  # tls_ctx
    ngtcp2_conn,  # conn
    POINTER(c_uint8),  # data
    c_size_t,  # datalen
    POINTER(ngtcp2_cid),  # scid (optional)
)


# TLS read callback type
TLSReadFunc = CFUNCTYPE(
    c_ssize_t,  # return: bytes read or error
    c_void_p,  # tls_ctx
    POINTER(c_uint8),  # buf
    c_size_t,  # len
    c_uint64,  # offset
)


# TLS write callback type
TLSWriteFunc = CFUNCTYPE(
    c_ssize_t,  # return: bytes written or error
    c_void_p,  # tls_ctx
    POINTER(c_uint8),  # buf
    c_size_t,  # len
    c_uint64,  # offset
)


def init_tls_backend() -> bool:
    """Initialize TLS backend for ngtcp2"""
    # Check NGTCP2_AVAILABLE dynamically (it might be set after import)
    try:
        from . import ngtcp2_bindings
        current_available = ngtcp2_bindings.NGTCP2_AVAILABLE
    except:
        current_available = NGTCP2_AVAILABLE
    
    if not current_available:
        logger.debug("init_tls_backend: NGTCP2_AVAILABLE is False")
        return False
    
    # Ensure crypto functions are bound (in case binding was deferred)
    if ngtcp2_crypto_ossl_init is None and ngtcp2_crypto_wolfssl_init is None:
        logger.debug("init_tls_backend: Binding crypto functions...")
        _bind_crypto_functions()
    
    # Check OpenSSL first
    if USE_OPENSSL:
        if ngtcp2_crypto_ossl_init:
            try:
                result = ngtcp2_crypto_ossl_init()
                if result == 0:
                    logger.info("Initialized ngtcp2 crypto (OpenSSL) backend")
                    return True
                else:
                    logger.error(f"Failed to initialize ngtcp2 crypto (OpenSSL): {result}")
                    return False
            except Exception as e:
                logger.error(f"Error calling ngtcp2_crypto_ossl_init: {e}")
                return False
        else:
            logger.debug("init_tls_backend: USE_OPENSSL=True but ngtcp2_crypto_ossl_init is None")
    
    # Check wolfSSL
    if USE_WOLFSSL:
        if ngtcp2_crypto_wolfssl_init:
            try:
                result = ngtcp2_crypto_wolfssl_init()
                if result == 0:
                    logger.info("Initialized ngtcp2 crypto (wolfSSL) backend")
                    return True
                else:
                    logger.error(f"Failed to initialize ngtcp2 crypto (wolfSSL): {result}")
                    return False
            except Exception as e:
                logger.error(f"Error calling ngtcp2_crypto_wolfssl_init: {e}")
                return False
        else:
            # Newer ngtcp2_crypto_wolfssl may not export ngtcp2_crypto_wolfssl_init; backend is still usable
            logger.info("Using ngtcp2 crypto (wolfSSL) backend (no init required)")
            return True

    logger.warning(f"No TLS backend available for ngtcp2 (USE_OPENSSL={USE_OPENSSL}, USE_WOLFSSL={USE_WOLFSSL}, ossl_init={ngtcp2_crypto_ossl_init is not None}, wolfssl_init={ngtcp2_crypto_wolfssl_init is not None})")
    return False


def verify_tls_bindings() -> bool:
    """Verify that TLS bindings are available"""
    if not NGTCP2_AVAILABLE:
        return False
    
    if USE_OPENSSL:
        if not OPENSSL_AVAILABLE:
            return False
        if not SSL_provide_quic_data:
            return False
        return True
    
    elif USE_WOLFSSL:
        if not WOLFSSL_AVAILABLE:
            return False
        if not wolfSSL_provide_quic_data:
            return False
        return True
    
    return False


# Global server TLS context (shared across all connections; OpenSSL 3.x implements TLS 1.3)
_server_tls_ctx = None

# wolfSSL server context creation (lazy-bound when USE_WOLFSSL)
wolfSSL_CTX_new = None
wolfSSL_CTX_free = None
wolfTLSv1_3_server_method = None
wolfSSL_CTX_use_certificate_file = None
wolfSSL_CTX_use_PrivateKey_file = None
ngtcp2_crypto_wolfssl_configure_server_context = None
wolfSSL_new = None
wolfSSL_free = None
wolfSSL_set_accept_state = None
wolfSSL_set_ex_data = None
wolfSSL_set_app_data = None  # ngtcp2 requires this for conn_ref (add_handshake_data uses SSL_get_app_data)
wolfSSL_set_quic_transport_version = None  # required so WolfSSL uses quic_method (add_handshake_data etc)
wolfSSL_Debugging_ON = None  # optional: enable WolfSSL debug to stderr when MQTTD_WOLFSSL_DEBUG=1
wolfSSL_CTX_set_alpn_select_cb = None  # ALPN selection callback (server)

# ALPN configuration (client expects "mqtt")
_ALPN_PROTO = b"mqtt"
_ALPN_BUF = (c_ubyte * len(_ALPN_PROTO))(*_ALPN_PROTO)
_ALPN_SELECT_CB = None

# PEM file type for wolfSSL (match OpenSSL SSL_FILETYPE_PEM = 1)
# QUIC v1 version (0x39) - must set on session so WolfSSL treats it as QUIC
WOLFSSL_QUIC_TRANSPORT_VERSION_V1 = 0x39
WOLFSSL_FILETYPE_PEM = 1


def _ensure_wolfssl_bound():
    """Ensure wolfSSL context-creation and ngtcp2_crypto_wolfssl_configure_server_context are bound."""
    global wolfSSL_CTX_new, wolfSSL_CTX_free, wolfTLSv1_3_server_method
    global wolfSSL_CTX_use_certificate_file, wolfSSL_CTX_use_PrivateKey_file
    global ngtcp2_crypto_wolfssl_configure_server_context
    global wolfSSL_new, wolfSSL_free, wolfSSL_set_accept_state, wolfSSL_set_ex_data, wolfSSL_set_app_data
    global wolfSSL_set_quic_transport_version, wolfSSL_CTX_set_alpn_select_cb
    global wolfSSL_Debugging_ON
    if ngtcp2_crypto_wolfssl_configure_server_context is not None and wolfSSL_CTX_new is not None:
        return
    _prepend_ld_library_path()
    _load_wolfssl_library()
    if not WOLFSSL_AVAILABLE or _wolfssl_lib is None:
        return
    w = _wolfssl_lib
    try:
        wolfTLSv1_3_server_method = w.wolfTLSv1_3_server_method
        wolfTLSv1_3_server_method.argtypes = []
        wolfTLSv1_3_server_method.restype = c_void_p
    except AttributeError:
        pass
    try:
        wolfSSL_CTX_new = w.wolfSSL_CTX_new
        wolfSSL_CTX_new.argtypes = [c_void_p]
        wolfSSL_CTX_new.restype = c_void_p
    except AttributeError:
        pass
    try:
        wolfSSL_CTX_free = w.wolfSSL_CTX_free
        wolfSSL_CTX_free.argtypes = [c_void_p]
        wolfSSL_CTX_free.restype = None
    except AttributeError:
        pass
    try:
        wolfSSL_CTX_use_certificate_file = w.wolfSSL_CTX_use_certificate_file
        wolfSSL_CTX_use_certificate_file.argtypes = [c_void_p, c_char_p, c_int]
        wolfSSL_CTX_use_certificate_file.restype = c_int
    except AttributeError:
        pass
    try:
        wolfSSL_CTX_use_PrivateKey_file = w.wolfSSL_CTX_use_PrivateKey_file
        wolfSSL_CTX_use_PrivateKey_file.argtypes = [c_void_p, c_char_p, c_int]
        wolfSSL_CTX_use_PrivateKey_file.restype = c_int
    except AttributeError:
        pass
    try:
        wolfSSL_new = w.wolfSSL_new
        wolfSSL_new.argtypes = [c_void_p]
        wolfSSL_new.restype = c_void_p
    except AttributeError:
        pass
    try:
        wolfSSL_free = w.wolfSSL_free
        wolfSSL_free.argtypes = [c_void_p]
        wolfSSL_free.restype = None
    except AttributeError:
        pass
    try:
        wolfSSL_set_accept_state = w.wolfSSL_set_accept_state
        wolfSSL_set_accept_state.argtypes = [c_void_p]
        wolfSSL_set_accept_state.restype = None
    except AttributeError:
        pass
    try:
        wolfSSL_set_ex_data = w.wolfSSL_set_ex_data
        wolfSSL_set_ex_data.argtypes = [c_void_p, c_int, c_void_p]
        wolfSSL_set_ex_data.restype = c_int
    except AttributeError:
        pass
    try:
        wolfSSL_set_app_data = w.wolfSSL_set_app_data
        wolfSSL_set_app_data.argtypes = [c_void_p, c_void_p]
        wolfSSL_set_app_data.restype = c_int
    except AttributeError:
        wolfSSL_set_app_data = None
    try:
        wolfSSL_set_quic_transport_version = w.wolfSSL_set_quic_transport_version
        wolfSSL_set_quic_transport_version.argtypes = [c_void_p, c_int]
        wolfSSL_set_quic_transport_version.restype = None
    except AttributeError:
        wolfSSL_set_quic_transport_version = None
    try:
        wolfSSL_CTX_set_alpn_select_cb = w.wolfSSL_CTX_set_alpn_select_cb
        wolfSSL_CTX_set_alpn_select_cb.argtypes = [c_void_p, c_void_p]
        wolfSSL_CTX_set_alpn_select_cb.restype = None
    except AttributeError:
        wolfSSL_CTX_set_alpn_select_cb = None
    try:
        wolfSSL_Debugging_ON = w.wolfSSL_Debugging_ON
        wolfSSL_Debugging_ON.argtypes = []
        wolfSSL_Debugging_ON.restype = None
    except AttributeError:
        wolfSSL_Debugging_ON = None
    if _ngtcp2_crypto_lib:
        try:
            f = getattr(_ngtcp2_crypto_lib, 'ngtcp2_crypto_wolfssl_configure_server_context', None)
            if f:
                ngtcp2_crypto_wolfssl_configure_server_context = f
                ngtcp2_crypto_wolfssl_configure_server_context.argtypes = [c_void_p]
                ngtcp2_crypto_wolfssl_configure_server_context.restype = c_int
        except Exception:
            pass


def _ensure_openssl_bound():
    """Ensure OpenSSL functions are bound - called lazily"""
    global TLS_server_method, SSL_CTX_new, SSL_CTX_free
    global SSL_CTX_use_certificate_file, SSL_CTX_use_PrivateKey_file
    global SSL_CTX_set_min_proto_version, SSL_CTX_set_alpn_select_cb
    global SSL_new, SSL_free, SSL_set_accept_state
    global SSL_set_app_data, SSL_get_app_data
    global ngtcp2_crypto_ossl_configure_server_session
    global ngtcp2_crypto_ossl_ctx_new, ngtcp2_crypto_ossl_ctx_set_ssl
    global ngtcp2_crypto_ossl_ctx_get_ssl, ngtcp2_crypto_ossl_ctx_del
    
    # Already bound?
    if TLS_server_method is not None:
        return
    
    # Load OpenSSL if needed
    if not OPENSSL_AVAILABLE or _openssl_lib is None:
        _load_openssl_library()
    
    if not OPENSSL_AVAILABLE or _openssl_lib is None:
        return
    
    ssl_lib = _openssl_lib
    
    try:
        TLS_server_method = ssl_lib.TLS_server_method
        TLS_server_method.argtypes = []
        TLS_server_method.restype = c_void_p
    except AttributeError:
        pass
    
    try:
        SSL_CTX_new = ssl_lib.SSL_CTX_new
        SSL_CTX_new.argtypes = [c_void_p]
        SSL_CTX_new.restype = c_void_p
    except AttributeError:
        pass
    
    try:
        SSL_CTX_free = ssl_lib.SSL_CTX_free
        SSL_CTX_free.argtypes = [c_void_p]
        SSL_CTX_free.restype = None
    except AttributeError:
        pass
    
    try:
        SSL_CTX_use_certificate_file = ssl_lib.SSL_CTX_use_certificate_file
        SSL_CTX_use_certificate_file.argtypes = [c_void_p, c_char_p, c_int]
        SSL_CTX_use_certificate_file.restype = c_int
    except AttributeError:
        pass
    
    try:
        SSL_CTX_use_PrivateKey_file = ssl_lib.SSL_CTX_use_PrivateKey_file
        SSL_CTX_use_PrivateKey_file.argtypes = [c_void_p, c_char_p, c_int]
        SSL_CTX_use_PrivateKey_file.restype = c_int
    except AttributeError:
        pass
    
    try:
        SSL_CTX_set_min_proto_version = ssl_lib.SSL_CTX_set_min_proto_version
        SSL_CTX_set_min_proto_version.argtypes = [c_void_p, c_int]
        SSL_CTX_set_min_proto_version.restype = c_int
    except AttributeError:
        pass
    try:
        SSL_CTX_set_alpn_select_cb = ssl_lib.SSL_CTX_set_alpn_select_cb
        SSL_CTX_set_alpn_select_cb.argtypes = [c_void_p, c_void_p, c_void_p]
        SSL_CTX_set_alpn_select_cb.restype = None
    except AttributeError:
        SSL_CTX_set_alpn_select_cb = None
    
    try:
        SSL_new = ssl_lib.SSL_new
        SSL_new.argtypes = [c_void_p]
        SSL_new.restype = c_void_p
    except AttributeError:
        pass
    
    try:
        SSL_free = ssl_lib.SSL_free
        SSL_free.argtypes = [c_void_p]
        SSL_free.restype = None
    except AttributeError:
        pass
    
    try:
        SSL_set_accept_state = ssl_lib.SSL_set_accept_state
        SSL_set_accept_state.argtypes = [c_void_p]
        SSL_set_accept_state.restype = None
    except AttributeError:
        pass
    
    # SSL_set_app_data / SSL_get_app_data (via ex_data with index 0)
    try:
        _ssl_set_ex_data = ssl_lib.SSL_set_ex_data
        _ssl_set_ex_data.argtypes = [c_void_p, c_int, c_void_p]
        _ssl_set_ex_data.restype = c_int
        # Create wrapper for SSL_set_app_data
        SSL_set_app_data = lambda ssl, data: _ssl_set_ex_data(ssl, 0, data)
    except AttributeError:
        pass
    
    try:
        _ssl_get_ex_data = ssl_lib.SSL_get_ex_data
        _ssl_get_ex_data.argtypes = [c_void_p, c_int]
        _ssl_get_ex_data.restype = c_void_p
        # Create wrapper for SSL_get_app_data
        SSL_get_app_data = lambda ssl: _ssl_get_ex_data(ssl, 0)
    except AttributeError:
        pass
    
    # Bind ngtcp2_crypto_ossl_configure_server_session from crypto library
    if _ngtcp2_crypto_lib is None:
        _load_ngtcp2_crypto_library()
    
    if _ngtcp2_crypto_lib:
        try:
            func = getattr(_ngtcp2_crypto_lib, 'ngtcp2_crypto_ossl_configure_server_session', None)
            if func:
                ngtcp2_crypto_ossl_configure_server_session = func
                ngtcp2_crypto_ossl_configure_server_session.argtypes = [c_void_p]
                ngtcp2_crypto_ossl_configure_server_session.restype = c_int
        except Exception:
            pass
        try:
            for name, (argtypes, restype) in [
                ('ngtcp2_crypto_ossl_ctx_new', ([POINTER(c_void_p), c_void_p], c_int)),  # possl_ctx (out), ssl
                ('ngtcp2_crypto_ossl_ctx_set_ssl', ([c_void_p, c_void_p], None)),
                ('ngtcp2_crypto_ossl_ctx_get_ssl', ([c_void_p], c_void_p)),
                ('ngtcp2_crypto_ossl_ctx_del', ([c_void_p], None)),
            ]:
                func = getattr(_ngtcp2_crypto_lib, name, None)
                if func:
                    func.argtypes = argtypes
                    func.restype = restype
                    globals()[name] = func
        except Exception:
            pass
    
    logger.debug("OpenSSL functions bound")


def create_server_tls_ctx(cert_file: str, key_file: str) -> Optional[c_void_p]:
    """
    Create and configure a TLS context for QUIC server (OpenSSL SSL_CTX or wolfSSL WOLFSSL_CTX).
    
    Args:
        cert_file: Path to certificate file (PEM format)
        key_file: Path to private key file (PEM format)
    
    Returns:
        TLS context handle (SSL_CTX* or WOLFSSL_CTX*) or None on failure
    """
    global _server_tls_ctx, _ALPN_SELECT_CB
    
    # wolfSSL path when USE_WOLFSSL
    if USE_WOLFSSL:
        _ensure_wolfssl_bound()
        if os.environ.get("MQTTD_WOLFSSL_DEBUG") and wolfSSL_Debugging_ON:
            try:
                wolfSSL_Debugging_ON()
                logger.info("WolfSSL debugging enabled (MQTTD_WOLFSSL_DEBUG=1)")
            except Exception:
                pass
        if (
            wolfSSL_CTX_new and wolfTLSv1_3_server_method
            and wolfSSL_CTX_use_certificate_file and wolfSSL_CTX_use_PrivateKey_file
            and ngtcp2_crypto_wolfssl_configure_server_context
        ):
            try:
                method = wolfTLSv1_3_server_method()
                if not method:
                    logger.error("wolfTLSv1_3_server_method() returned NULL")
                    return None
                ctx = wolfSSL_CTX_new(method)
                if not ctx:
                    logger.error("wolfSSL_CTX_new() returned NULL")
                    return None
                r = wolfSSL_CTX_use_certificate_file(ctx, cert_file.encode(), WOLFSSL_FILETYPE_PEM)
                if r != 1:
                    logger.error(f"Failed to load certificate from {cert_file}")
                    if wolfSSL_CTX_free:
                        wolfSSL_CTX_free(ctx)
                    return None
                r = wolfSSL_CTX_use_PrivateKey_file(ctx, key_file.encode(), WOLFSSL_FILETYPE_PEM)
                if r != 1:
                    logger.error(f"Failed to load private key from {key_file}")
                    if wolfSSL_CTX_free:
                        wolfSSL_CTX_free(ctx)
                    return None
                result = ngtcp2_crypto_wolfssl_configure_server_context(ctx)
                if result != 0:
                    logger.error(f"ngtcp2_crypto_wolfssl_configure_server_context failed: {result}")
                    if wolfSSL_CTX_free:
                        wolfSSL_CTX_free(ctx)
                    return None
                # Configure ALPN: select "mqtt" when offered by client
                if wolfSSL_CTX_set_alpn_select_cb:
                    if _ALPN_SELECT_CB is None:
                        @CFUNCTYPE(c_int, c_void_p, POINTER(POINTER(c_ubyte)), POINTER(c_ubyte), POINTER(c_ubyte), c_uint, c_void_p)
                        def _alpn_select_cb(ssl, out, outlen, in_buf, inlen, arg):
                            data = ctypes.string_at(in_buf, inlen)
                            i = 0
                            while i + 1 <= len(data):
                                ln = data[i]
                                i += 1
                                if i + ln > len(data):
                                    break
                                if data[i:i+ln] == _ALPN_PROTO:
                                    out[0] = ctypes.cast(_ALPN_BUF, POINTER(c_ubyte))
                                    outlen[0] = len(_ALPN_PROTO)
                                    return 0
                                i += ln
                            return 1
                        _ALPN_SELECT_CB = _alpn_select_cb
                    wolfSSL_CTX_set_alpn_select_cb(ctx, ctypes.cast(_ALPN_SELECT_CB, c_void_p))
                    logger.info("Configured ALPN selection for wolfSSL: mqtt")
                _server_tls_ctx = ctx
                logger.info("Created server TLS context (wolfSSL) successfully")
                return ctx
            except Exception as e:
                logger.error(f"Error creating wolfSSL TLS context: {e}")
                return None
        if not WOLFSSL_AVAILABLE:
            logger.error("wolfSSL not available for TLS context creation (install libwolfssl and set LD_LIBRARY_PATH)")
        else:
            logger.error("wolfSSL server context helpers not available (missing wolfTLSv1_3_server_method or ngtcp2_crypto_wolfssl_configure_server_context)")
        return None
    
    # OpenSSL path
    _ensure_openssl_bound()
    
    if not USE_OPENSSL or not OPENSSL_AVAILABLE:
        logger.error("OpenSSL not available for TLS context creation")
        return None
    
    if not TLS_server_method or not SSL_CTX_new:
        logger.error("Required OpenSSL (TLS) functions not available")
        return None
    
    try:
        # Create TLS context via OpenSSL SSL_CTX (TLS_server_method)
        method = TLS_server_method()
        if not method:
            logger.error("TLS_server_method() returned NULL")
            return None
        
        ctx = SSL_CTX_new(method)
        if not ctx:
            logger.error("SSL_CTX_new() returned NULL")
            return None
        
        # Load certificate
        if SSL_CTX_use_certificate_file:
            result = SSL_CTX_use_certificate_file(ctx, cert_file.encode(), SSL_FILETYPE_PEM)
            if result != 1:
                logger.error(f"Failed to load certificate from {cert_file}")
                if SSL_CTX_free:
                    SSL_CTX_free(ctx)
                return None
        
        # Load private key
        if SSL_CTX_use_PrivateKey_file:
            result = SSL_CTX_use_PrivateKey_file(ctx, key_file.encode(), SSL_FILETYPE_PEM)
            if result != 1:
                logger.error(f"Failed to load private key from {key_file}")
                if SSL_CTX_free:
                    SSL_CTX_free(ctx)
                return None
        
        # Set minimum TLS version to 1.3 (required for QUIC)
        if SSL_CTX_set_min_proto_version:
            SSL_CTX_set_min_proto_version(ctx, TLS1_3_VERSION)

        # Configure ALPN: select "mqtt" when offered by client
        if SSL_CTX_set_alpn_select_cb:
            if _ALPN_SELECT_CB is None:
                @CFUNCTYPE(c_int, c_void_p, POINTER(POINTER(c_ubyte)), POINTER(c_ubyte), POINTER(c_ubyte), c_uint, c_void_p)
                def _alpn_select_cb(ssl, out, outlen, in_buf, inlen, arg):
                    data = ctypes.string_at(in_buf, inlen)
                    i = 0
                    while i + 1 <= len(data):
                        ln = data[i]
                        i += 1
                        if i + ln > len(data):
                            break
                        if data[i:i+ln] == _ALPN_PROTO:
                            out[0] = ctypes.cast(_ALPN_BUF, POINTER(c_ubyte))
                            outlen[0] = len(_ALPN_PROTO)
                            return 0
                        i += ln
                    return 1
                _ALPN_SELECT_CB = _alpn_select_cb
            SSL_CTX_set_alpn_select_cb(ctx, ctypes.cast(_ALPN_SELECT_CB, c_void_p), None)
            logger.info("Configured ALPN selection for OpenSSL: mqtt")
        
        _server_tls_ctx = ctx
        logger.info("Created server TLS context successfully")
        return ctx
    
    except Exception as e:
        logger.error(f"Error creating TLS context: {e}")
        return None


# Backward-compatible names (SSL -> TLS in our API)
create_server_ssl_ctx = create_server_tls_ctx


def create_server_tls_session(tls_ctx: Optional[c_void_p] = None) -> Optional[c_void_p]:
    """
    Create a TLS session for a new QUIC connection.
    OpenSSL: returns crypto_ossl_ctx*. WolfSSL: returns WOLFSSL*.
    
    Args:
        tls_ctx: TLS context to use (uses global if None)
    
    Returns:
        Session handle for ngtcp2_conn_set_tls_native_handle, or None on failure.
    """
    ctx = tls_ctx or _server_tls_ctx
    if not ctx:
        logger.error("No TLS context available")
        return None
    
    # wolfSSL path
    if USE_WOLFSSL:
        _ensure_wolfssl_bound()
        if wolfSSL_new and wolfSSL_set_accept_state:
            try:
                ssl = wolfSSL_new(ctx)
                if not ssl:
                    logger.error("wolfSSL_new() returned NULL")
                    return None
                wolfSSL_set_accept_state(ssl)
                # Required so WolfSSL uses quic_method (add_handshake_data etc); see ngtcp2 tls_server_session_wolfssl.cc
                if wolfSSL_set_quic_transport_version:
                    wolfSSL_set_quic_transport_version(ssl, WOLFSSL_QUIC_TRANSPORT_VERSION_V1)
                    logger.info("Set WolfSSL session to QUIC v1 (required for add_handshake_data)")
                else:
                    logger.warning(
                        "wolfSSL_set_quic_transport_version not found; rebuild WolfSSL with --enable-quic. "
                        "Handshake will not complete."
                    )
                logger.debug("Created WOLFSSL session for QUIC server")
                return ssl
            except Exception as e:
                logger.error(f"Error creating wolfSSL session: {e}")
                return None
        return None
    
    # OpenSSL path
    _ensure_openssl_bound()
    
    if not SSL_new:
        logger.error("OpenSSL SSL_new not available")
        return None
    
    try:
        # libngtcp2_crypto_ossl expects tls_native_handle = crypto_ossl_ctx*, not SSL*
        # ngtcp2_crypto_ossl_ctx_new(possl_ctx, ssl) allocates ctx and stores in *possl_ctx
        if ngtcp2_crypto_ossl_ctx_new:
            ssl = SSL_new(ctx)
            if not ssl:
                logger.error("SSL_new() returned NULL")
                return None
            if SSL_set_accept_state:
                SSL_set_accept_state(ssl)
            if ngtcp2_crypto_ossl_configure_server_session:
                result = ngtcp2_crypto_ossl_configure_server_session(ssl)
                if result != 0:
                    logger.error(f"ngtcp2_crypto_ossl_configure_server_session failed: {result}")
                    if SSL_free:
                        SSL_free(ssl)
                    return None
            ossl_ctx_out = c_void_p()
            ret = ngtcp2_crypto_ossl_ctx_new(byref(ossl_ctx_out), ssl)
            if ret != 0 or not ossl_ctx_out.value:
                logger.error("ngtcp2_crypto_ossl_ctx_new failed (ret=%s, ctx=%s)", ret, ossl_ctx_out.value)
                if SSL_free:
                    SSL_free(ssl)
                return None
            logger.debug("Created crypto_ossl_ctx for QUIC server")
            return ossl_ctx_out.value
        # Fallback: return raw SSL* (may crash in crypto callbacks if lib expects ossl_ctx)
        session = SSL_new(ctx)
        if not session:
            logger.error("SSL_new() returned NULL")
            return None
        if SSL_set_accept_state:
            SSL_set_accept_state(session)
        if ngtcp2_crypto_ossl_configure_server_session:
            result = ngtcp2_crypto_ossl_configure_server_session(session)
            if result != 0:
                logger.error(f"ngtcp2_crypto_ossl_configure_server_session failed: {result}")
                if SSL_free:
                    SSL_free(session)
                return None
        return session
    except Exception as e:
        logger.error(f"Error creating TLS session: {e}")
        return None


# Backward-compatible name
create_server_ssl_session = create_server_tls_session


def free_tls_session(session: c_void_p) -> None:
    """Free a TLS session. OpenSSL: crypto_ossl_ctx* or SSL*. WolfSSL: WOLFSSL*."""
    if not session:
        return
    if USE_WOLFSSL:
        _ensure_wolfssl_bound()
        if wolfSSL_free:
            try:
                wolfSSL_free(session)
            except Exception:
                pass
        return
    if ngtcp2_crypto_ossl_ctx_del:
        try:
            ngtcp2_crypto_ossl_ctx_del(session)  # frees internal SSL and buffers
            return
        except Exception:
            pass
    if SSL_free:
        SSL_free(session)


# Backward-compatible name
free_ssl_session = free_tls_session


# Initialize on import if available
if NGTCP2_AVAILABLE and (USE_OPENSSL or USE_WOLFSSL):
    init_tls_backend()

# Callback binding function - called lazily to avoid circular import issues
def _ensure_callbacks_bound():
    """Bind crypto callbacks - must be called after ngtcp2 library is fully loaded"""
    global ngtcp2_crypto_recv_client_initial_cb, ngtcp2_crypto_recv_crypto_data_cb
    global ngtcp2_crypto_encrypt_cb, ngtcp2_crypto_decrypt_cb, ngtcp2_crypto_hp_mask_cb
    global ngtcp2_crypto_update_key_cb
    global ngtcp2_crypto_delete_crypto_aead_ctx_cb, ngtcp2_crypto_delete_crypto_cipher_ctx_cb
    global ngtcp2_crypto_get_path_challenge_data_cb
    
    # Already bound?
    if ngtcp2_crypto_recv_client_initial_cb is not None:
        return
    
    # Load crypto library if not loaded
    if _ngtcp2_crypto_lib is None:
        _load_ngtcp2_crypto_library()
    
    if _ngtcp2_crypto_lib is None:
        return
    
    _crypto = _ngtcp2_crypto_lib
    try:
        _func = getattr(_crypto, 'ngtcp2_crypto_recv_client_initial_cb', None)
        if _func:
            ngtcp2_crypto_recv_client_initial_cb = ctypes.cast(_func, c_void_p).value
        _func = getattr(_crypto, 'ngtcp2_crypto_recv_crypto_data_cb', None)
        if _func:
            ngtcp2_crypto_recv_crypto_data_cb = ctypes.cast(_func, c_void_p).value
        _func = getattr(_crypto, 'ngtcp2_crypto_encrypt_cb', None)
        if _func:
            ngtcp2_crypto_encrypt_cb = ctypes.cast(_func, c_void_p).value
        _func = getattr(_crypto, 'ngtcp2_crypto_decrypt_cb', None)
        if _func:
            ngtcp2_crypto_decrypt_cb = ctypes.cast(_func, c_void_p).value
        _func = getattr(_crypto, 'ngtcp2_crypto_hp_mask_cb', None)
        if _func:
            ngtcp2_crypto_hp_mask_cb = ctypes.cast(_func, c_void_p).value
        _func = getattr(_crypto, 'ngtcp2_crypto_update_key_cb', None)
        if _func:
            ngtcp2_crypto_update_key_cb = ctypes.cast(_func, c_void_p).value
        _func = getattr(_crypto, 'ngtcp2_crypto_delete_crypto_aead_ctx_cb', None)
        if _func:
            ngtcp2_crypto_delete_crypto_aead_ctx_cb = ctypes.cast(_func, c_void_p).value
        _func = getattr(_crypto, 'ngtcp2_crypto_delete_crypto_cipher_ctx_cb', None)
        if _func:
            ngtcp2_crypto_delete_crypto_cipher_ctx_cb = ctypes.cast(_func, c_void_p).value
        _func = getattr(_crypto, 'ngtcp2_crypto_get_path_challenge_data_cb', None)
        if _func:
            ngtcp2_crypto_get_path_challenge_data_cb = ctypes.cast(_func, c_void_p).value
    except Exception:
        pass  # Silently ignore errors


if __name__ == "__main__":
    print(f"OpenSSL available: {OPENSSL_AVAILABLE}")
    print(f"wolfSSL available: {WOLFSSL_AVAILABLE}")
    print(f"ngtcp2 crypto available: {NGTCP2_CRYPTO_AVAILABLE}")
    print(f"TLS bindings verified: {verify_tls_bindings()}")
    
    if USE_OPENSSL:
        print("Using OpenSSL backend")
    elif USE_WOLFSSL:
        print("Using wolfSSL backend")
    else:
        print("No TLS backend configured")
