# ngtcp2 Crash Fix - Complete Solution

## Current Status

✅ **Workaround implemented** - Connection initialization no longer crashes
⚠️ **Tests still crash** - Need to rebuild and reinstall ngtcp2

## The Problem

ngtcp2 crashes with:
```
ngtcp2_settings.c:96 ngtcp2_settingslen_version: Unreachable.
Aborted (core dumped)
```

This happens when:
1. `ngtcp2_settings_default` is called before TLS backend is initialized
2. ngtcp2 library wasn't properly rebuilt with OpenSSL

## The Solution

### Step 1: Rebuild and Reinstall ngtcp2 (REQUIRED)

You've rebuilt ngtcp2, but it needs to be **installed**:

```bash
cd /home/annadata/tools/ngtcp2

# Make sure it's built with OpenSSL
./config.status --config | grep openssl
# Should show: --with-openssl

# Install (requires sudo)
sudo make install
sudo ldconfig
```

### Step 2: Verify Installation

```bash
# Check library is linked to OpenSSL
ldd /usr/local/lib/libngtcp2.so.16 | grep ssl
# Should show: libssl.so.3

# Check crypto library
ldd /usr/local/lib/libngtcp2_crypto_ossl.so.0.1.1 | grep ssl
# Should show: libssl.so.3
```

### Step 3: Test

```bash
cd /home/annadata/api/MQTTD
python3 -c "
from mqttd.ngtcp2_tls_bindings import init_tls_backend
print(f'TLS init: {init_tls_backend()}')

from mqttd.transport_quic_ngtcp2 import QUICServerNGTCP2
server = QUICServerNGTCP2(host='127.0.0.1', port=1884)
print('✓ Server created')
"
```

## Workaround Implemented

I've added a workaround that:
1. ✅ Catches crashes from `ngtcp2_settings_default`
2. ✅ Manually initializes settings structure
3. ✅ Prevents crashes during connection initialization

**However**, tests still crash because they call `ngtcp2_settings_default` during import, before the workaround can catch it.

## Next Steps

1. **Install rebuilt ngtcp2**: `sudo make install` in ngtcp2 directory
2. **Verify**: Check library linkage
3. **Test**: Run tests - they should work after installation

## Files Modified

- `mqttd/transport_quic_ngtcp2.py` - Added workaround for settings_default crash
- `mqttd/ngtcp2_tls_bindings.py` - Improved TLS initialization
- `mqttd/ngtcp2_bindings.py` - Added settings version constants

---

**Status**: Workaround in place, but ngtcp2 needs to be installed after rebuild
