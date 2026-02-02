#!/bin/bash
#
# Build WolfSSL (TLS 1.3 + QUIC) and ngtcp2 with WolfSSL for mqttd server.
# Uses same versions as client: ref-code/capacitor-mqtt-quic/deps-versions.sh (and VERSION.txt).
# WolfSSL is the default TLS backend; same commit/tag as client for compatibility.
#
# Requires: autoconf, automake, libtool, cmake, pkg-config
# Usage: ./scripts/build-wolfssl-ngtcp2.sh [--prefix /usr/local]
#
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
MQTTD_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PREFIX="${PREFIX:-/usr/local}"
# Source mqttd deps-versions.sh (prefer client's when present; same versions as client)
REF_ROOT="$(cd "$MQTTD_ROOT/../.." 2>/dev/null && pwd)" || true
if [ -f "$MQTTD_ROOT/deps-versions.sh" ]; then
    . "$MQTTD_ROOT/deps-versions.sh"
fi
USE_WOLFSSL="${USE_WOLFSSL:-1}"
WOLFSSL_TAG="${WOLFSSL_TAG:-v5.8.4-stable}"
NGTCP2_COMMIT="${NGTCP2_COMMIT:-3ce3bbead}"
ENABLE_QUIC="${ENABLE_QUIC:-1}"
# Default paths: wolfssl-<tag without v> (e.g. wolfssl-5.8.4-stable), ngtcp2 at ref-code/ngtcp2
WOLFSSL_DIRNAME="wolfssl-${WOLFSSL_TAG#v}"
WOLFSSL_SRC="${WOLFSSL_SOURCE_DIR:-$REF_ROOT/$WOLFSSL_DIRNAME}"
NGTCP2_SRC="${NGTCP2_SOURCE_DIR:-$REF_ROOT/ngtcp2}"

while [[ $# -gt 0 ]]; do
    case $1 in
        --prefix) PREFIX="$2"; shift 2 ;;
        --wolfssl-src) WOLFSSL_SRC="$2"; shift 2 ;;
        --ngtcp2-src) NGTCP2_SRC="$2"; shift 2 ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

echo "Building WolfSSL (TLS 1.3 + QUIC) and ngtcp2 for mqttd (same versions as client by default)"
echo "  WolfSSL source: $WOLFSSL_SRC (tag: ${WOLFSSL_TAG})"
echo "  ngtcp2 source:  $NGTCP2_SRC (commit: ${NGTCP2_COMMIT})"
echo "  Install prefix: $PREFIX"

# 1. Build and install WolfSSL (same version as client: deps-versions.sh / VERSION.txt)
if [ ! -d "$WOLFSSL_SRC" ]; then
    echo "Error: WolfSSL source not found at $WOLFSSL_SRC"
    echo "Use same version as client: $WOLFSSL_TAG (see ref-code/VERSION.txt or capacitor-mqtt-quic/deps-versions.sh)"
    echo "Download: curl -L -O https://github.com/wolfSSL/wolfssl/archive/refs/tags/${WOLFSSL_TAG}.tar.gz"
    echo "Extract:  tar xzf ${WOLFSSL_TAG}.tar.gz -C ${REF_ROOT:-.}  # -> $WOLFSSL_DIRNAME"
    exit 1
fi
cd "$WOLFSSL_SRC"
# Only run autogen.sh if configure is missing (tarballs include pre-generated configure)
if [ ! -f ./configure ]; then
    if [ -f ./autogen.sh ]; then
        # macOS Homebrew: aclocal needs libtool m4 in path (LT_INIT)
        if command -v brew &>/dev/null; then
            BREW_LIBTOOL="$(brew --prefix libtool 2>/dev/null)"
            [ -n "$BREW_LIBTOOL" ] && [ -d "$BREW_LIBTOOL/share/aclocal" ] && \
                export ACLOCAL_PATH="$BREW_LIBTOOL/share/aclocal${ACLOCAL_PATH:+:$ACLOCAL_PATH}"
        fi
        ./autogen.sh
    fi
fi
[ ! -f ./configure ] && { echo "Error: configure not found"; exit 1; }
# Static lib must be built with -fPIC so ngtcp2 can link it into shared libs
export CFLAGS="${CFLAGS:-} -fPIC"
WOLFSSL_QUIC_OPT=""
[ "$ENABLE_QUIC" = "1" ] && WOLFSSL_QUIC_OPT="--enable-quic"
./configure \
    --prefix="$PREFIX" \
    $WOLFSSL_QUIC_OPT \
    --enable-tls13 \
    --disable-shared \
    --enable-static \
    --disable-examples \
    --disable-crypttests \
    --enable-opensslall \
    --enable-base64encode
# Rebuild objects with current CFLAGS (e.g. -fPIC) if Makefile changed
make clean 2>/dev/null || true
make -j$(nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 4)
sudo make install
sudo ldconfig 2>/dev/null || true

# 2. Build and install ngtcp2 with WolfSSL
if [ ! -d "$NGTCP2_SRC" ]; then
    echo "Error: ngtcp2 source not found at $NGTCP2_SRC"
    echo "Clone: git clone ${NGTCP2_REPO_URL:-https://github.com/ngtcp2/ngtcp2.git} $NGTCP2_SRC"
    echo "Then:  cd $NGTCP2_SRC && git checkout $NGTCP2_COMMIT"
    exit 1
fi
# Pin ngtcp2 to same commit as client (from deps-versions.sh)
if [ -d "$NGTCP2_SRC/.git" ] && [ -n "$NGTCP2_COMMIT" ]; then
    (cd "$NGTCP2_SRC" && git fetch origin 2>/dev/null; git checkout "$NGTCP2_COMMIT") || true
fi
export PKG_CONFIG_PATH="$PREFIX/lib/pkgconfig:$PKG_CONFIG_PATH"
mkdir -p "$NGTCP2_SRC/build" && cd "$NGTCP2_SRC/build"
cmake .. \
    -DCMAKE_INSTALL_PREFIX="$PREFIX" \
    -DENABLE_LIB_ONLY=ON \
    -DBUILD_TESTING=OFF \
    -DENABLE_WOLFSSL=ON \
    -DENABLE_OPENSSL=OFF
make -j$(nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 4)
sudo make install
sudo ldconfig 2>/dev/null || true

echo ""
echo "Build complete. Verify with:"
echo "  pkg-config --exists wolfssl && echo 'WolfSSL OK'"
echo "  pkg-config --exists libngtcp2_crypto_wolfssl && echo 'ngtcp2+wolfssl OK'"
echo "  python3 -c \"from mqttd.ngtcp2_tls_bindings import init_tls_backend, USE_WOLFSSL; print('wolfSSL:', USE_WOLFSSL, init_tls_backend())\""
