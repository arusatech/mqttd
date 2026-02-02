#!/bin/bash
#
# Pinned repo URLs and versions for mqttd server (WolfSSL, ngtcp2).
# Same values as client (capacitor-mqtt-quic) for compatibility; see ref-code/VERSION.txt.
# Prefer client's file when present: source ref-code/capacitor-mqtt-quic/deps-versions.sh.
#
set -e

# When sourced from scripts/build-wolfssl-ngtcp2.sh, $0 is ref-code/mqttd/deps-versions.sh
MQTTD_ROOT="$(cd "$(dirname "$0")" && pwd)"
REF_ROOT="$(cd "$MQTTD_ROOT/../.." 2>/dev/null && pwd)" || true
CLIENT_DEPS="${REF_ROOT}/capacitor-mqtt-quic/deps-versions.sh"

if [ -f "$CLIENT_DEPS" ]; then
    . "$CLIENT_DEPS"
    return 0 2>/dev/null || true
fi

# Standalone defaults (match ref-code/capacitor-mqtt-quic/deps-versions.sh and ref-code/VERSION.txt)
export USE_WOLFSSL="${USE_WOLFSSL:-1}"
export ENABLE_QUIC="${ENABLE_QUIC:-1}"
export NGHTTP3_REPO_URL="${NGHTTP3_REPO_URL:-https://github.com/ngtcp2/nghttp3.git}"
export NGHTTP3_COMMIT="${NGHTTP3_COMMIT:-78f27c1}"
export NGTCP2_REPO_URL="${NGTCP2_REPO_URL:-https://github.com/ngtcp2/ngtcp2.git}"
export NGTCP2_COMMIT="${NGTCP2_COMMIT:-3ce3bbead}"
export WOLFSSL_REPO_URL="${WOLFSSL_REPO_URL:-https://github.com/wolfSSL/wolfssl.git}"
export WOLFSSL_TAG="${WOLFSSL_TAG:-v5.8.4-stable}"
export QUICTLS_REPO_URL="${QUICTLS_REPO_URL:-https://github.com/quictls/quictls.git}"
export OPENSSL_COMMIT="${OPENSSL_COMMIT:-2cc13b7c86fd76e5b45b5faa4ca365a602f92392}"
