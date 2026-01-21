# wolfSSL Installation Steps - Ready to Install

## Current Status

✅ **wolfSSL has been built successfully** with QUIC support
⚠️ **Installation requires sudo** (password needed)

## Installation Commands

Run these commands manually (they require sudo password):

```bash
# 1. Install wolfSSL
cd /home/annadata/tools/wolfssl
sudo make install

# 2. Update library cache
sudo ldconfig

# 3. Verify installation
wolfssl-config --version
pkg-config --modversion wolfssl

# 4. Check QUIC support
wolfssl-config --cflags | grep -i quic
```

## After Installation: Configure ngtcp2 with wolfSSL

Once wolfSSL is installed, reconfigure ngtcp2:

```bash
# 1. Set PKG_CONFIG_PATH
export PKG_CONFIG_PATH=/usr/local/lib/pkgconfig:$PKG_CONFIG_PATH

# 2. Clean ngtcp2 build
cd /home/annadata/tools/ngtcp2
make distclean 2>/dev/null || rm -rf autom4te.cache config.cache

# 3. Regenerate build files
autoreconf -i

# 4. Configure with wolfSSL
./configure \
    --prefix=/usr/local \
    --enable-lib-only \
    --with-wolfssl=/usr/local \
    LDFLAGS="-Wl,-rpath,/usr/local/lib"

# 5. Verify wolfSSL is detected
./config.log | grep -i "wolfssl.*yes"
# Should show: "wolfSSL: yes"

# 6. Build and install
make -j$(nproc)
sudo make install
sudo ldconfig
```

## Verification After ngtcp2 Rebuild

```bash
# Check ngtcp2 is linked to wolfSSL
ldd /usr/local/lib/libngtcp2.so.16 | grep wolfssl
# Should show: libwolfssl.so

# Test Python
python3 -c "
from mqttd.ngtcp2_tls_bindings import init_tls_backend, USE_WOLFSSL
print(f'wolfSSL: {USE_WOLFSSL}')
print(f'TLS init: {init_tls_backend()}')
"
# Should print: wolfSSL: True, TLS init: True

# Test server creation
python3 -c "
from mqttd.transport_quic_ngtcp2 import QUICServerNGTCP2
server = QUICServerNGTCP2(host='127.0.0.1', port=1884)
print('✓ Server created successfully!')
"
```

## Quick Install Script

Save this as `install_wolfssl.sh` and run it:

```bash
#!/bin/bash
set -e

echo "Installing wolfSSL..."
cd /home/annadata/tools/wolfssl
sudo make install
sudo ldconfig

echo "Verifying installation..."
wolfssl-config --version
pkg-config --modversion wolfssl

echo "✓ wolfSSL installed successfully!"
echo ""
echo "Next: Reconfigure ngtcp2 with wolfSSL (see steps above)"
```

---

**Status**: wolfSSL built successfully, ready for installation  
**Next Step**: Run `sudo make install` in `/home/annadata/tools/wolfssl`
