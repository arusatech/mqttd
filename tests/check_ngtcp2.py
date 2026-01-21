#!/usr/bin/env python3
"""
Check if ngtcp2 is properly configured before running tests

This script checks if ngtcp2 can be safely imported and used.
If it crashes, we know tests will crash too.
"""

import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Skip TLS initialization
os.environ['MQTTD_SKIP_TLS_INIT'] = '1'

def check_ngtcp2_safe():
    """Check if ngtcp2 can be safely imported"""
    try:
        # Try importing just the bindings (should be safe)
        from mqttd.ngtcp2_bindings import NGTCP2_AVAILABLE, get_ngtcp2_lib
        if not NGTCP2_AVAILABLE:
            return False, "ngtcp2 library not available"
        
        # Try getting the library (should be safe)
        lib = get_ngtcp2_lib()
        if lib is None:
            return False, "ngtcp2 library could not be loaded"
        
        # Try importing transport module (this might crash)
        try:
            # CRITICAL: Initialize TLS backend BEFORE importing transport module
            # This prevents crashes when ngtcp2_settings_default is called
            try:
                from mqttd.ngtcp2_tls_bindings import init_tls_backend
                init_tls_backend()
            except:
                pass  # Continue even if TLS init fails
            
            from mqttd.transport_quic_ngtcp2 import QUICServerNGTCP2
            # If we get here, import worked
            # Now try creating an instance (this is where it crashes)
            try:
                server = QUICServerNGTCP2(host="127.0.0.1", port=1884)
                return True, "ngtcp2 is properly configured"
            except (SystemError, OSError, RuntimeError) as e:
                return False, f"ngtcp2 crashes during initialization: {e}"
            except Exception as e:
                # Other errors are OK - at least it didn't crash
                return True, f"ngtcp2 import works (warning: {e})"
        except (SystemError, OSError) as e:
            return False, f"ngtcp2 crashes during import: {e}"
        except ImportError as e:
            return False, f"ngtcp2 import failed: {e}"
            
    except ImportError as e:
        return False, f"Cannot import ngtcp2 bindings: {e}"
    except Exception as e:
        return False, f"Unexpected error: {e}"

if __name__ == '__main__':
    safe, message = check_ngtcp2_safe()
    if safe:
        print(f"✓ {message}")
        sys.exit(0)
    else:
        print(f"✗ {message}")
        print("\nTests will be skipped. To fix:")
        print("1. Install ngtcp2 with proper TLS backend support")
        print("2. Initialize TLS backend before using ngtcp2")
        print("3. See tests/TESTING_NOTES.md for details")
        sys.exit(1)
