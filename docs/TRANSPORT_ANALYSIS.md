# MQTT Transport Analysis - TCP vs QUIC/ngtcp2

## Current Implementation Status

### ✅ QUIC/ngtcp2 Support EXISTS

The project **already has QUIC support implemented** with three implementations:

1. **`transport_quic_ngtcp2.py`** - Production-grade ngtcp2 implementation ✅
2. **`transport_quic_pure.py`** - Pure Python QUIC (fallback)
3. **`transport_quic.py`** - aioquic implementation (fallback)

### ❌ Issue: TCP is Hardcoded as Primary Transport

**Current Behavior:**
- **TCP server is ALWAYS started** (line 1556-1562 in `app.py`)
- **QUIC server is OPTIONAL** and runs in parallel (if `enable_quic=True`)
- Both servers run simultaneously when QUIC is enabled

**Code Location:**
```python
# mqttd/app.py lines 1556-1562
# Create TCP server - ALWAYS RUNS
self._server = await asyncio.start_server(
    self._handle_client,
    self.host,
    self.port,
    ssl=self.ssl_context
)

# QUIC server - OPTIONAL (lines 1534-1554)
if self.enable_quic and MQTTQuicServer:
    # Starts QUIC server in parallel
```

## Architecture Analysis

### Transport Layer Structure

```
MQTT Protocol Layer (app.py)
    ↓
Transport Abstraction
    ├── TCP Transport (asyncio.start_server) ← ALWAYS ACTIVE
    │   └── StreamReader/StreamWriter
    │
    └── QUIC Transport (optional, parallel)
        ├── ngtcp2 (production-grade) ← PRIORITY 1
        ├── Pure Python QUIC ← PRIORITY 2
        └── aioquic ← PRIORITY 3
        └── StreamReader/StreamWriter adapters
```

### MQTT Handler Integration

**Good News:** The MQTT handlers are **transport-agnostic**!

- TCP uses: `asyncio.StreamReader` / `asyncio.StreamWriter`
- QUIC uses: `NGTCP2StreamReader` / `NGTCP2StreamWriter` (implements same interface)
- Both call: `self._handle_client(reader, writer, ...)`

**This means:** The MQTT protocol code works with both TCP and QUIC without modification!

## Current QUIC Implementation Details

### ngtcp2 Implementation (`transport_quic_ngtcp2.py`)

**Status:** ✅ Fully implemented
- Uses ngtcp2 C library bindings (`ngtcp2_bindings.py`)
- TLS 1.3 support (`ngtcp2_tls_bindings.py`)
- Connection management
- Stream management
- Packet handling
- **Reuses MQTT handlers** via adapter pattern

**Key Files:**
- `mqttd/transport_quic_ngtcp2.py` (892 lines)
- `mqttd/ngtcp2_bindings.py` (416 lines)
- `mqttd/ngtcp2_tls_bindings.py` (TLS integration)

### Integration Points

1. **QUIC Server Setup** (app.py:1534-1554):
   ```python
   if self.enable_quic and MQTTQuicServer:
       self._quic_server = MQTTQuicServer(...)
       self._quic_server.set_mqtt_handler(self._handle_client)  # ← Reuses TCP handler!
   ```

2. **MQTT Handler Reuse** (transport_quic_ngtcp2.py:873-893):
   ```python
   async def _handle_mqtt_over_quic(self, connection, stream):
       reader = NGTCP2StreamReader(stream)  # Adapter
       writer = NGTCP2StreamWriter(connection, stream, self)  # Adapter
       await self.mqtt_handler(reader, writer, connection)  # ← Same handler!
   ```

## Recommendations

### Option 1: Make QUIC-Only Mode (Recommended)

Add a `transport_mode` parameter to allow QUIC-only operation:

```python
MQTTApp(
    transport_mode="quic",  # "tcp", "quic", or "both" (default: "tcp")
    enable_quic=True,
    quic_port=1884,
    ...
)
```

**Changes needed:**
1. Add `transport_mode` parameter to `__init__`
2. Conditionally start TCP server based on mode
3. Make QUIC required when `transport_mode="quic"`

### Option 2: Make TCP Optional

Add `enable_tcp` flag (default: True for backward compatibility):

```python
MQTTApp(
    enable_tcp=False,  # Disable TCP
    enable_quic=True,  # Use QUIC only
    quic_port=1884,
    ...
)
```

### Option 3: Transport Selection at Runtime

Allow selecting transport per connection or via configuration.

## Implementation Plan for QUIC-Only Mode

### Step 1: Update `MQTTApp.__init__`

```python
def __init__(self,
             ...
             transport_mode: str = "tcp",  # "tcp", "quic", or "both"
             enable_tcp: bool = True,  # For backward compatibility
             enable_quic: bool = False,
             ...):
    # Validate transport mode
    if transport_mode == "quic":
        enable_quic = True
        enable_tcp = False
    elif transport_mode == "both":
        enable_tcp = True
        enable_quic = True
    
    self.enable_tcp = enable_tcp
    self.enable_quic = enable_quic
```

### Step 2: Update `_start_server` Method

```python
async def _start_server(self):
    # Start QUIC server first (if enabled)
    if self.enable_quic and MQTTQuicServer:
        # ... existing QUIC startup code ...
    
    # Start TCP server (if enabled)
    if self.enable_tcp:
        self._server = await asyncio.start_server(...)
        # ... existing TCP startup code ...
    else:
        # If QUIC-only, wait for QUIC server
        if not self.enable_quic:
            raise ValueError("At least one transport must be enabled")
```

### Step 3: Update Documentation

Update README to show QUIC-only usage:

```python
# QUIC-only mode
app = MQTTApp(
    transport_mode="quic",
    quic_port=1884,
    quic_certfile="cert.pem",
    quic_keyfile="key.pem",
)
```

## Current ngtcp2 Status

### ✅ What's Implemented

- Full ngtcp2 bindings (`ngtcp2_bindings.py`)
- TLS 1.3 integration (`ngtcp2_tls_bindings.py`)
- QUIC server with ngtcp2 (`transport_quic_ngtcp2.py`)
- Stream reader/writer adapters
- MQTT handler integration
- Connection and stream management

### ⚠️ Requirements

- ngtcp2 C library must be installed
- nghttp3 C library must be installed
- TLS certificates required (QUIC requires TLS 1.3)

### 📝 Usage (Current - Parallel Mode)

```python
# Currently runs BOTH TCP and QUIC
app = MQTTApp(
    port=1883,  # TCP port
    enable_quic=True,  # Also start QUIC
    quic_port=1884,  # QUIC port
    quic_certfile="cert.pem",
    quic_keyfile="key.pem",
)
```

## Summary

**Answer to your question:**

1. **MQTT is NOT hardcoded to TCP** - QUIC support exists ✅
2. **However, TCP is ALWAYS started** - QUIC is optional and parallel ❌
3. **ngtcp2 implementation EXISTS** - Fully implemented in `transport_quic_ngtcp2.py` ✅
4. **To use QUIC-only:** Need to modify `app.py` to make TCP optional

**Recommendation:** Implement Option 1 or 2 to allow QUIC-only mode while maintaining backward compatibility.
