# ngtcp2 TLS Backend Configuration Guide

This guide explains how to properly configure ngtcp2 with TLS backend support (OpenSSL or wolfSSL) to fix the crash issue and enable QUIC functionality.

## Overview

ngtcp2 **requires** a TLS backend to function. Without proper TLS backend initialization, ngtcp2 will crash with:
```
ngtcp2_settings.c:96 ngtcp2_settingslen_version: Unreachable.
Aborted (core dumped)
```

You can use either **OpenSSL** or **wolfSSL** as the TLS backend.

---

## Option 1: Configure with OpenSSL (Recommended)

OpenSSL is the most common TLS library and is usually already installed on most systems.

### Step 1: Check OpenSSL Installation

```bash
# Check if OpenSSL is installed
openssl version

# Check if OpenSSL 3.x is installed (required for QUIC)
openssl version | grep -E "OpenSSL 3\.[0-9]"

# Check if OpenSSL development headers are available
pkg-config --modversion openssl
```

**Requirements:**
- OpenSSL 3.0.0 or later (for QUIC support)
- Development headers (`libssl-dev` or `openssl-devel` package)

### Step 2: Install OpenSSL Development Headers (if needed)

**On Ubuntu/Debian:**
```bash
sudo apt-get update
sudo apt-get install libssl-dev
```

**On CentOS/RHEL:**
```bash
sudo yum install openssl-devel
# OR for newer versions
sudo dnf install openssl-devel
```

**On macOS:**
```bash
brew install openssl
```

### Step 3: Rebuild ngtcp2 with OpenSSL

```bash
cd /home/annadata/tools/ngtcp2

# Clean previous build
make distclean 2>/dev/null || rm -rf autom4te.cache config.cache

# Regenerate build files
autoreconf -i

# Configure with OpenSSL
./configure \
    --prefix=/usr/local \
    --enable-lib-only \
    --with-openssl

# Verify OpenSSL is detected
./config.log | grep -i openssl
# Should show: "OpenSSL: yes"

# Build
make -j$(nproc)

# Install
sudo make install

# Update library cache
sudo ldconfig
```

### Step 4: Verify ngtcp2 with OpenSSL

```bash
# Check ngtcp2 version
pkg-config --modversion libngtcp2

# Check if OpenSSL is linked
ldd /usr/local/lib/libngtcp2.so | grep ssl
# Should show: libssl.so.3 or similar

# Test Python import
python3 -c "
from mqttd.ngtcp2_tls_bindings import init_tls_backend, USE_OPENSSL
print(f'OpenSSL available: {USE_OPENSSL}')
print(f'TLS init result: {init_tls_backend()}')
"
```

---

## Option 2: Configure with wolfSSL

See [WOLFSSL_INSTALLATION.md](../WOLFSSL_INSTALLATION.md) for detailed wolfSSL setup.

### Quick wolfSSL Setup

```bash
cd /home/annadata/tools

# Clone and build wolfSSL
git clone https://github.com/wolfSSL/wolfssl.git
cd wolfssl
./autogen.sh
./configure --enable-quic --enable-opensslextra --enable-tls13 --prefix=/usr/local
make -j$(nproc)
sudo make install
sudo ldconfig

# Rebuild ngtcp2 with wolfSSL
cd /home/annadata/tools/ngtcp2
make distclean 2>/dev/null || rm -rf autom4te.cache config.cache
autoreconf -i
export PKG_CONFIG_PATH=/usr/local/lib/pkgconfig:$PKG_CONFIG_PATH
./configure --prefix=/usr/local --enable-lib-only --with-wolfssl=/usr/local
make -j$(nproc)
sudo make install
sudo ldconfig
```

---

## Verification Checklist

After configuration, verify everything works:

### 1. Check ngtcp2 Library

```bash
# Check library exists
ls -lh /usr/local/lib/libngtcp2.so*

# Check pkg-config
pkg-config --modversion libngtcp2
```

### 2. Check TLS Backend

```bash
# For OpenSSL
ldd /usr/local/lib/libngtcp2.so | grep ssl

# For wolfSSL
ldd /usr/local/lib/libngtcp2.so | grep wolfssl
```

### 3. Test Python Bindings

```python
# Test TLS backend initialization
from mqttd.ngtcp2_tls_bindings import init_tls_backend, USE_OPENSSL, USE_WOLFSSL
print(f"OpenSSL: {USE_OPENSSL}, wolfSSL: {USE_WOLFSSL}")
result = init_tls_backend()
print(f"TLS init: {result}")  # Should be True

# Test server creation (should not crash)
from mqttd.transport_quic_ngtcp2 import QUICServerNGTCP2
server = QUICServerNGTCP2(host="127.0.0.1", port=1884)
print("Server created successfully!")
```

### 4. Run Tests

```bash
# Tests should now work without crashing
python tests/run_tests_safe.py
```

---

## Troubleshooting

### Issue: "checking for openssl >= 3.0.0... no"

**Solution:**
```bash
# Install OpenSSL development headers
sudo apt-get install libssl-dev  # Ubuntu/Debian
sudo yum install openssl-devel   # CentOS/RHEL
```

### Issue: "ngtcp2_settings.c:96 ngtcp2_settingslen_version: Unreachable"

**This means TLS backend is not initialized.**

**Solution:**
1. Rebuild ngtcp2 with TLS backend (see steps above)
2. Ensure TLS backend is initialized: `init_tls_backend()` should return `True`
3. Verify library is linked: `ldd /usr/local/lib/libngtcp2.so | grep ssl`

### Issue: Library not found at runtime

**Solution:**
```bash
# Update LD_LIBRARY_PATH
export LD_LIBRARY_PATH=/usr/local/lib:$LD_LIBRARY_PATH

# Or rebuild with rpath
./configure --with-openssl LDFLAGS="-Wl,-rpath,/usr/local/lib"
```

---

## Quick Setup Script

```bash
#!/bin/bash
set -e

echo "Setting up ngtcp2 with OpenSSL..."

# Install OpenSSL dev headers (if needed)
if ! pkg-config --exists openssl; then
    echo "Installing OpenSSL development headers..."
    sudo apt-get update
    sudo apt-get install -y libssl-dev
fi

# Rebuild ngtcp2
cd /home/annadata/tools/ngtcp2

echo "Cleaning previous build..."
make distclean 2>/dev/null || rm -rf autom4te.cache config.cache

echo "Regenerating build files..."
autoreconf -i

echo "Configuring with OpenSSL..."
./configure --prefix=/usr/local --enable-lib-only --with-openssl

echo "Building..."
make -j$(nproc)

echo "Installing..."
sudo make install
sudo ldconfig

echo "Verifying..."
pkg-config --modversion libngtcp2
ldd /usr/local/lib/libngtcp2.so | grep ssl

echo "✓ ngtcp2 configured with OpenSSL successfully!"
```

---

## References

- [ngtcp2 GitHub](https://github.com/ngtcp2/ngtcp2)
- [WOLFSSL_INSTALLATION.md](../WOLFSSL_INSTALLATION.md) - Detailed wolfSSL guide
- [NGTCP2_IMPLEMENTATION_PLAN.md](NGTCP2_IMPLEMENTATION_PLAN.md) - Full implementation plan

---

**Last Updated:** January 2025
