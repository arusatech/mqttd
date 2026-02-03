# Build mqttd Server First

This doc describes how to **build the mqttd server first**: C dependencies (WolfSSL + ngtcp2) then the Python package. Use this when you want to run MQTT-over-QUIC with ngtcp2 and WolfSSL (TLS 1.3 + QUIC).

---

## Prerequisites

- **ref-code layout** (recommended):
  - `ref-code/mqttd` – this repo
  - `ref-code/wolfssl-5.8.4-stable` – WolfSSL source (or extract [v5.8.4-stable tarball](https://github.com/wolfSSL/wolfssl/archive/refs/tags/v5.8.4-stable.tar.gz) into `ref-code/`)
  - `ref-code/ngtcp2` – ngtcp2 source (`git clone https://github.com/ngtcp2/ngtcp2.git ref-code/ngtcp2`)
- **Build tools**: autoconf, automake, libtool, cmake, pkg-config
- **Python**: 3.7+ (3.13+ recommended)
- **Optional**: libtool m4 path on macOS – `brew install libtool`; if `./autogen.sh` fails in WolfSSL, set `ACLOCAL_PATH="$(brew --prefix libtool)/share/aclocal:$ACLOCAL_PATH"` and re-run

---

## One-command build (recommended)

From **mqttd root**:

```bash
./scripts/build-server.sh
```

This will:

1. Build and install **WolfSSL** (TLS 1.3 + QUIC) and **ngtcp2** with WolfSSL to `/usr/local` (uses `scripts/build-wolfssl-ngtcp2.sh`; **sudo** required for `make install` and `ldconfig`).
2. Install the **Python package** in editable mode: `pip install -e .`
3. Print verification and next steps.

Custom install prefix (no sudo):

```bash
./scripts/build-server.sh --prefix $HOME/.local
export PKG_CONFIG_PATH=$HOME/.local/lib/pkgconfig
export LD_LIBRARY_PATH=$HOME/.local/lib
```

---

## Manual build (two steps)

### Step 1: WolfSSL + ngtcp2 (C libs)

```bash
cd ref-code/mqttd
./scripts/build-wolfssl-ngtcp2.sh [--prefix /usr/local]
```

- Requires WolfSSL source at `ref-code/wolfssl-5.8.4-stable` and ngtcp2 at `ref-code/ngtcp2` (or pass `--wolfssl-src` / `--ngtcp2-src`).
- Versions are taken from `mqttd/deps-versions.sh` (same as client; see `ref-code/VERSION.txt`).
- **sudo** is used for `make install` and `ldconfig` when prefix is `/usr/local`.

### Step 2: Python package

```bash
cd ref-code/mqttd
pip install -e .
```

---

## Verify

```bash
pkg-config --exists wolfssl && echo "WolfSSL OK"
pkg-config --exists libngtcp2_crypto_wolfssl && echo "ngtcp2+wolfssl OK"
# With LD_LIBRARY_PATH so Python loads /usr/local WolfSSL ngtcp2 crypto
export LD_LIBRARY_PATH=/usr/local/lib64:/usr/local/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}
python3 -c "from mqttd.ngtcp2_tls_bindings import init_tls_backend, USE_WOLFSSL; print('wolfSSL:', USE_WOLFSSL, init_tls_backend())"
```
Expect `wolfSSL: True True`. If you see `wolfSSL: False`, the process did not load libngtcp2_crypto_wolfssl (set `LD_LIBRARY_PATH` and try again).

---

## Run the server (QUIC)

1. **TLS certs** (required for QUIC):

   ```bash
   openssl req -x509 -newkey rsa:2048 -nodes -keyout key.pem -out cert.pem -days 365 -subj /CN=localhost
   ```

2. **Use WolfSSL ngtcp2 at runtime** – On RHEL/AlmaLinux/Fedora the libs install to `/usr/local/lib64`. The dynamic linker must see them (and their deps: libwolfssl, libngtcp2). Set `LD_LIBRARY_PATH` **before** starting the server:

   ```bash
   export LD_LIBRARY_PATH=/usr/local/lib64:/usr/local/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}
   ```

   Optional: require WolfSSL and fail if not found: `export MQTTD_QUIC_USE_WOLFSSL=1`

3. **Run an example** (from mqttd root):

   ```bash
   python3 examples/mqtt_quic_server.py
   ```

   Or QUIC-only:

   ```bash
   python3 examples/mqtt_quic_only_server.py
   ```

   Ensure `quic_certfile` / `quic_keyfile` in the example point to your cert and key (e.g. `cert.pem`, `key.pem`). You should see **"Bound ngtcp2_crypto_wolfssl_init"** in the log; if you see "Bound ngtcp2_crypto_ossl_init", WolfSSL was not loaded (check `LD_LIBRARY_PATH`).

---

## Troubleshooting

- **WolfSSL source not found**: Extract WolfSSL into `ref-code/wolfssl-5.8.4-stable` or set `WOLFSSL_SOURCE_DIR` / use `--wolfssl-src`.
- **ngtcp2 source not found**: `git clone https://github.com/ngtcp2/ngtcp2.git ref-code/ngtcp2`; the build script will checkout the pinned commit from `deps-versions.sh`.
- **aclocal/libtool (macOS)**: Install libtool and, if needed, set `ACLOCAL_PATH` as in Prerequisites.
- **pkg-config not found after custom prefix**: Set `PKG_CONFIG_PATH` and `LD_LIBRARY_PATH` to your prefix’s `lib/pkgconfig` and `lib`.

- **"Bound ngtcp2_crypto_ossl_init" / server using OpenSSL**: The server prefers WolfSSL but **could not find** `libngtcp2_crypto_wolfssl.so`. Install WolfSSL + ngtcp2 on the **server machine**: run `./scripts/build-wolfssl-ngtcp2.sh` (from mqttd root, with ref-code layout). On RHEL/AlmaLinux/Fedora the install goes to `/usr/local/lib64`; mqttd looks there first. If needed, start the server with `LD_LIBRARY_PATH=/usr/local/lib64` (or `/usr/local/lib` on macOS). Until WolfSSL ngtcp2 is installed, the server falls back to OpenSSL and you may see NGTCP2_ERR_TRANSPORT_PARAM (-225) with the capacitor-mqtt-quic client.

**NGTCP2_ERR_TRANSPORT_PARAM (-225)** – client params failed validation: (1) Use the same ngtcp2 version on client and server: build server with `./scripts/build-wolfssl-ngtcp2.sh` (uses `NGTCP2_COMMIT` from deps-versions.sh). (2) Use WolfSSL on the server so it matches the client: install WolfSSL + ngtcp2 on the server (see above); on RHEL/AlmaLinux use `LD_LIBRARY_PATH=/usr/local/lib64` (or `/usr/local/lib`) when starting if the loader does not find the libs. (3) Same ngtcp2 commit on both sides avoids transport-param encoding/validation mismatches.
