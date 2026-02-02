#!/bin/bash
#
# Build the mqttd server first: WolfSSL + ngtcp2 (C libs), then Python package.
# Uses existing scripts/build-wolfssl-ngtcp2.sh; same versions as client (deps-versions.sh).
#
# Prerequisites:
#   - ref-code layout: ref-code/mqttd, ref-code/wolfssl-5.8.4-stable, ref-code/ngtcp2
#   - autoconf, automake, libtool, cmake, pkg-config
#   - Python 3.7+
#
# Usage:
#   ./scripts/build-server.sh [--prefix /usr/local]
#
# Example (install to /usr/local):
#   ./scripts/build-server.sh
#
# Example (install to custom prefix, no sudo):
#   ./scripts/build-server.sh --prefix $HOME/.local
#   export PKG_CONFIG_PATH=$HOME/.local/lib/pkgconfig
#   export LD_LIBRARY_PATH=$HOME/.local/lib
#
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
MQTTD_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

echo "=== Building mqttd server (WolfSSL + ngtcp2, then Python) ==="
echo "  mqttd root: $MQTTD_ROOT"
echo ""

# 1. Build and install WolfSSL + ngtcp2 (requires sudo for default /usr/local)
echo "--- Step 1: WolfSSL + ngtcp2 (C libraries) ---"
"$SCRIPT_DIR/build-wolfssl-ngtcp2.sh" "$@"
echo ""

# 2. Install Python package (editable)
echo "--- Step 2: Python package (pip install -e .) ---"
cd "$MQTTD_ROOT"
pip install -e . 2>/dev/null || pip3 install -e .
echo ""

# 3. Verify (pkg-config may need PKG_CONFIG_PATH if you used --prefix)
echo "--- Verify ---"
if pkg-config --exists wolfssl 2>/dev/null; then
    echo "  WolfSSL: $(pkg-config --modversion wolfssl)"
else
    echo "  WolfSSL: pkg-config not found (set PKG_CONFIG_PATH if using custom prefix)"
fi
if pkg-config --exists libngtcp2_crypto_wolfssl 2>/dev/null; then
    echo "  ngtcp2+crypto: OK"
else
    echo "  ngtcp2+crypto: pkg-config not found (set PKG_CONFIG_PATH if using custom prefix)"
fi
python3 -c "
from mqttd.ngtcp2_tls_bindings import init_tls_backend, USE_WOLFSSL
ok = init_tls_backend()
print('  Python mqttd: USE_WOLFSSL=%s, init_tls_backend=%s' % (USE_WOLFSSL, ok))
" 2>/dev/null || echo "  Python mqttd: run 'python3 -c \"from mqttd.ngtcp2_tls_bindings import init_tls_backend; print(init_tls_backend())\"' to verify"
echo ""

echo "=== Server build complete ==="
echo "Next:"
echo "  1. Generate TLS certs for QUIC (e.g. cert.pem, key.pem)"
echo "  2. Run an example:"
echo "     cd $MQTTD_ROOT"
echo "     python3 examples/mqtt_quic_server.py"
echo "  Or QUIC-only: examples/mqtt_quic_only_server.py"
echo ""
