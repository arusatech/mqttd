"""
ngtcp2 TLS Integration Bindings - Phase 2 Implementation
Based on curl's curl_ngtcp2.c and vquic-tls.c reference implementation

This module provides Python ctypes bindings for TLS library integration with ngtcp2.
Supports OpenSSL and wolfSSL.

Reference:
- curl/lib/vquic/curl_ngtcp2.c
- curl/lib/vquic/vquic-tls.c
- ngtcp2 crypto API: https://nghttp2.org/ngtcp2/
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
            try:
                _openssl_lib.SSL_library_init
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


def _load_ngtcp2_crypto_library():
    """Load ngtcp2 crypto library (OpenSSL or wolfSSL backend)"""
    global _ngtcp2_crypto_lib, NGTCP2_CRYPTO_AVAILABLE, USE_OPENSSL, USE_WOLFSSL
    
    if _ngtcp2_crypto_lib is not None:
        return NGTCP2_CRYPTO_AVAILABLE
    
    # Try to determine which TLS backend ngtcp2 was built with
    # Based on curl's implementation, we check for specific symbols
    
    lib_names = [
        'libngtcp2_crypto_ossl.so',
        'libngtcp2_crypto_ossl.so.0',
        'libngtcp2_crypto_ossl.so.0.1.1',  # Full version
        'libngtcp2_crypto_wolfssl.so',
        'libngtcp2_crypto_wolfssl.so.0',
        'libngtcp2_crypto_quictls.so',
        'libngtcp2_crypto_quictls.so.0',
    ]
    
    # Also try with full path
    import os
    full_paths = [
        '/usr/local/lib/libngtcp2_crypto_ossl.so',
        '/usr/local/lib/libngtcp2_crypto_ossl.so.0',
        '/usr/local/lib/libngtcp2_crypto_ossl.so.0.1.1',
        '/usr/local/lib/libngtcp2_crypto_wolfssl.so',
        '/usr/local/lib/libngtcp2_crypto_wolfssl.so.0',
    ]
    
    all_lib_names = lib_names + full_paths
    
    for lib_name in all_lib_names:
        try:
            _ngtcp2_crypto_lib = CDLL(lib_name)
            # Try to find initialization function
            try:
                # Check for OpenSSL backend
                if 'ossl' in lib_name or 'quictls' in lib_name:
                    _ngtcp2_crypto_lib.ngtcp2_crypto_ossl_init
                    NGTCP2_CRYPTO_AVAILABLE = True
                    USE_OPENSSL = True  # Set USE_OPENSSL when we find ossl library
                    logger.info(f"Loaded ngtcp2 crypto (OpenSSL) library: {lib_name}")
                    return True
                elif 'wolfssl' in lib_name:
                    _ngtcp2_crypto_lib.ngtcp2_crypto_wolfssl_init
                    NGTCP2_CRYPTO_AVAILABLE = True
                    USE_WOLFSSL = True  # Set USE_WOLFSSL when we find wolfssl library
                    logger.info(f"Loaded ngtcp2 crypto (wolfSSL) library: {lib_name}")
                    return True
            except AttributeError:
                _ngtcp2_crypto_lib = None
                continue
        except OSError:
            continue
    
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


# OpenSSL QUIC API (OpenSSL 3.2+)
if USE_OPENSSL and OPENSSL_AVAILABLE and _openssl_lib:
    ssl_lib = _openssl_lib
    
    # SSL context types (simplified)
    SSL_CTX = c_void_p
    SSL = c_void_p
    
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

# Bind crypto functions - this runs after libraries are loaded
def _bind_crypto_functions():
    """Bind ngtcp2 crypto functions after library is loaded"""
    global ngtcp2_crypto_ossl_init, ngtcp2_crypto_wolfssl_init
    
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
                return True
            else:
                logger.debug("ngtcp2_crypto_ossl_init function not found")
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
                return True
        except Exception as e:
            logger.debug(f"Error binding ngtcp2_crypto_wolfssl_init: {e}")
    
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
            logger.debug("init_tls_backend: USE_WOLFSSL=True but ngtcp2_crypto_wolfssl_init is None")
    
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


# Initialize on import if available
if NGTCP2_AVAILABLE and (USE_OPENSSL or USE_WOLFSSL):
    init_tls_backend()


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
