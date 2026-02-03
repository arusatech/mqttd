#!/bin/bash
# Build ngtcp2_wrapper shared library

set -e

cd "$(dirname "$0")"

NGTCP2_DIR="/home/annadata/api/mqttSrv/ref-code/ngtcp2"
WRAPPER_SRC="mqttd/ngtcp2_wrapper.c"
WRAPPER_LIB="mqttd/libngtcp2_wrapper.so"

echo "Building ngtcp2 wrapper library..."

gcc -shared -fPIC \
    -I"$NGTCP2_DIR/lib/includes" \
    -I"$NGTCP2_DIR/lib" \
    -L"$NGTCP2_DIR/build/lib" \
    -o "$WRAPPER_LIB" \
    "$WRAPPER_SRC" \
    -lngtcp2

echo "✓ Built $WRAPPER_LIB"
echo ""
echo "Now install mqttd with: pip install -e ."
