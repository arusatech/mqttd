# wolfSSL Installation Guide for ngtcp2

This guide explains how to install wolfSSL for use with ngtcp2 in your MQTT over QUIC implementation.

## Prerequisites

- Build tools: `gcc`, `make`, `autotools` (autoconf, automake, libtool)
- Git

## Installation Steps

### 1. Clone wolfSSL Repository

```bash
cd /home/annadata/tools
git clone https://github.com/wolfSSL/wolfssl.git
cd wolfssl
```

### 2. Checkout Latest Stable Release (Optional but Recommended)

```bash
# List available releases
git tag | grep -E "^v[0-9]" | sort -V | tail -10

# Checkout latest stable (example - adjust version number)
git checkout v5.7.4-stable  # Use latest stable version
```

### 3. Build and Configure

```bash
# Generate build files
./autogen.sh

# Configure with QUIC support
./configure \
    --enable-quic \
    --enable-opensslextra \
    --enable-tls13 \
    --prefix=/usr/local

# Alternative: configure for development/testing
./configure \
    --enable-quic \
    --enable-opensslextra \
    --enable-tls13 \
    --enable-all \
    --prefix=/usr/local
```

**Configuration Options:**
- `--enable-quic`: Enable QUIC support (required for ngtcp2)
- `--enable-opensslextra`: Enable OpenSSL compatibility API
- `--enable-tls13`: Enable TLS 1.3 support (required for QUIC)
- `--enable-all`: Enable all features (for testing/development)
- `--prefix=/usr/local`: Installation prefix

### 4. Build and Install

```bash
# Build with parallel compilation (faster)
make -j$(nproc)

# Install (requires sudo)
sudo make install

# Update library cache
sudo ldconfig
```

### 5. Verify Installation

```bash
# Check version
wolfssl-config --version

# Check if QUIC support is enabled
wolfssl-config --cflags | grep -i quic
```

### 6. Configure ngtcp2 with wolfSSL

```bash
cd /home/annadata/tools/ngtcp2

# Clean previous configuration
make distclean 2>/dev/null || rm -rf autom4te.cache config.cache

# Configure with wolfSSL
./configure \
    --with-wolfssl=/usr/local \
    PKG_CONFIG_PATH=/usr/local/lib/pkgconfig:$PKG_CONFIG_PATH

# Verify wolfSSL is detected
./config.log | grep -i wolfssl
```

### 7. Rebuild ngtcp2

```bash
make -j$(nproc)
sudo make install
sudo ldconfig
```

## Configuration for ngtcp2

After installing wolfSSL, reconfigure ngtcp2:

```bash
cd /home/annadata/tools/ngtcp2
./configure \
    --with-wolfssl=/usr/local \
    PKG_CONFIG_PATH=/usr/local/lib/pkgconfig:$PKG_CONFIG_PATH \
    LDFLAGS="-Wl,-rpath,/usr/local/lib"
```

**Note**: The `PKG_CONFIG_PATH` helps ngtcp2's configure script find wolfSSL's pkg-config files.

## Verification

After installation, verify that ngtcp2 is configured with wolfSSL:

```bash
cd /home/annadata/tools/ngtcp2
./configure --with-wolfssl=/usr/local 2>&1 | grep -A 10 "wolfSSL"
```

You should see:
```
wolfSSL:        yes (CFLAGS='...' LIBS='-lwolfssl')
```

## Troubleshooting

### Issue: "Package 'wolfssl', required by 'virtual:world', not found"

**Solution**: Set PKG_CONFIG_PATH:
```bash
export PKG_CONFIG_PATH=/usr/local/lib/pkgconfig:$PKG_CONFIG_PATH
```

### Issue: "checking for wolfssl >= 5.5.0... no"

**Solution**: 
1. Make sure you installed wolfSSL with `--enable-quic`
2. Verify pkg-config can find it:
```bash
pkg-config --modversion wolfssl
```

### Issue: Library not found at runtime

**Solution**: Update LD_LIBRARY_PATH or use rpath:
```bash
export LD_LIBRARY_PATH=/usr/local/lib:$LD_LIBRARY_PATH

# Or rebuild ngtcp2 with rpath:
./configure --with-wolfssl=/usr/local \
    LDFLAGS="-Wl,-rpath,/usr/local/lib"
```

### Issue: QUIC API not found

**Solution**: Make sure you built wolfSSL with `--enable-quic`:
```bash
# Reconfigure and rebuild wolfSSL
cd /home/annadata/tools/wolfssl
make distclean
./configure --enable-quic --enable-opensslextra --enable-tls13 --prefix=/usr/local
make -j$(nproc)
sudo make install
sudo ldconfig
```

## Quick Installation Script

Here's a complete script to install wolfSSL and configure ngtcp2:

```bash
#!/bin/bash
set -e

# Install wolfSSL
cd /home/annadata/tools
git clone https://github.com/wolfSSL/wolfssl.git
cd wolfssl
./autogen.sh
./configure --enable-quic --enable-opensslextra --enable-tls13 --prefix=/usr/local
make -j$(nproc)
sudo make install
sudo ldconfig

# Configure ngtcp2 with wolfSSL
cd /home/annadata/tools/ngtcp2
export PKG_CONFIG_PATH=/usr/local/lib/pkgconfig:$PKG_CONFIG_PATH
./configure --with-wolfssl=/usr/local \
    LDFLAGS="-Wl,-rpath,/usr/local/lib"
make -j$(nproc)
sudo make install
sudo ldconfig

echo "✓ wolfSSL and ngtcp2 installed successfully!"
```

## References

- [wolfSSL GitHub](https://github.com/wolfSSL/wolfssl)
- [wolfSSL QUIC Support](https://www.wolfssl.com/quic-with-wolfssl/)
- [ngtcp2 Documentation](https://github.com/ngtcp2/ngtcp2)
- [curl HTTP/3 Documentation](https://curl.se/docs/http3.html)
