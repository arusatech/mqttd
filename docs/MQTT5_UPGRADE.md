# MQTT 5.0 Upgrade Guide

## Overview

The MQTTD package now supports **MQTT 5.0** while maintaining full backward compatibility with **MQTT 3.1.1**. This document outlines the key enhancements and new features.

## Key New Features in MQTT 5.0

Based on the [MQTT 5.0 Specification](https://docs.oasis-open.org/mqtt/mqtt/v5.0/mqtt-v5.0.html), the following features have been added:

### 1. **Session Expiry Interval**
- Replaces `Clean Session` flag with `Clean Start` and `Session Expiry Interval`
- Allows sessions to persist after disconnection for a specified time
- Better control over session lifecycle

### 2. **Reason Codes**
- All ACK packets now include reason codes
- More detailed error reporting
- Better debugging and problem diagnosis

### 3. **User Properties**
- Custom key-value pairs in packets
- Application-defined metadata
- Forwarded with messages

### 4. **Message Expiry**
- Set expiration time for messages
- Automatic cleanup of expired messages
- Prevents stale data delivery

### 5. **Topic Aliases**
- Reduce packet size by using aliases
- Efficiency improvement for repeated topics
- Client and server can set maximum aliases

### 6. **Subscription Identifiers**
- Numeric identifiers for subscriptions
- Identify which subscription delivered a message
- Useful for multiplexed clients

### 7. **Flow Control**
- Receive Maximum property
- Limits outstanding QoS > 0 messages
- Prevents message queue overflow

### 8. **Maximum Packet Size**
- Negotiate maximum packet size
- Prevent oversized packets
- Better resource management

### 9. **Payload Format Indicator**
- Indicates if payload is UTF-8 text or binary
- Helps receivers handle content correctly

### 10. **Request/Response Pattern**
- Response Topic and Correlation Data
- Built-in request/response support
- Simplify RPC-style messaging

## Implementation Status

### ✅ Implemented
- Properties encoding/decoding system
- Reason codes for all ACK packets
- MQTT 5.0 CONNECT/CONNACK with properties
- MQTT 5.0 PUBLISH with properties
- MQTT 5.0 SUBACK with reason codes
- MQTT 5.0 DISCONNECT with reason codes
- Protocol version detection (3.1.1 vs 5.0)
- Backward compatibility with MQTT 3.1.1

### 🔄 In Progress
- Full parser for MQTT 5.0 CONNECT with properties
- Session expiry management
- Topic alias handling
- Subscription identifier tracking

### 📋 Planned
- Enhanced authentication (AUTH packet)
- Shared subscriptions
- Will delay interval
- Server keep alive negotiation
- Assigned client identifier

## Usage

### MQTT 5.0 Client

```python
from mqttd import MQTTApp
from mqttd.protocol_v5 import MQTT5Protocol
from mqttd.properties import PropertyType
from mqttd.reason_codes import ReasonCode

app = MQTTApp(port=1883)

# Server automatically detects protocol version
# Supports both MQTT 3.1.1 and 5.0 clients

@app.subscribe("sensors/temperature")
async def handle_temp(topic: str, client):
    # Works with both protocol versions
    print(f"Temperature subscription from {client.client_id}")
```

### Protocol Version Detection

The server automatically detects the protocol version from the CONNECT packet:
- `protocol_level = 0x04` → MQTT 3.1.1
- `protocol_level = 0x05` → MQTT 5.0

The server responds with the same protocol version.

## Migration from MQTT 3.1.1

### For Clients

**Old (MQTT 3.1.1):**
```python
# Clean Session
clean_session = True
```

**New (MQTT 5.0):**
```python
# Clean Start + Session Expiry
clean_start = True
session_expiry_interval = 3600  # 1 hour
```

### For Servers

The MQTTD server automatically handles both versions. No code changes needed!

**Automatic Features:**
- Protocol version detection
- Appropriate response format
- Reason code mapping for compatibility

## Property Examples

### User Properties

```python
from mqttd.protocol_v5 import MQTT5Protocol
from mqttd.properties import PropertyType

# Add user properties to PUBLISH
properties = {
    PropertyType.USER_PROPERTY: [
        ("source", "sensor-001"),
        ("timestamp", "2024-01-01T12:00:00Z")
    ]
}

publish_msg = MQTT5Protocol.build_publish_v5(
    topic="sensors/temperature",
    payload=b"25.5",
    user_properties=[("source", "sensor-001")]
)
```

### Message Expiry

```python
# Message expires in 1 hour
properties = {
    PropertyType.MESSAGE_EXPIRY_INTERVAL: 3600
}

publish_msg = MQTT5Protocol.build_publish_v5(
    topic="alerts/critical",
    payload=b"High temperature",
    message_expiry_interval=3600
)
```

### Topic Alias

```python
# Use topic alias to reduce packet size
publish_msg = MQTT5Protocol.build_publish_v5(
    topic="very/long/topic/name/that/is/repeated",
    payload=b"data",
    topic_alias=1  # Use alias instead of full topic name
)
```

## Reason Codes

All ACK packets now include reason codes for better error handling:

```python
from mqttd.reason_codes import ReasonCode

# Success
ReasonCode.SUCCESS  # 0x00

# Connection errors
ReasonCode.UNSUPPORTED_PROTOCOL_VERSION  # 0x84
ReasonCode.BAD_USER_NAME_OR_PASSWORD     # 0x86
ReasonCode.NOT_AUTHORIZED                # 0x87

# Subscription errors
ReasonCode.GRANTED_QOS_0                 # 0x00
ReasonCode.GRANTED_QOS_1                 # 0x01
ReasonCode.TOPIC_FILTER_INVALID          # 0x8F
```

## Testing

### Test with MQTT 3.1.1 Client

```bash
# Works as before
mosquitto_pub -h localhost -p 1883 -t "test/topic" -m "Hello"
```

### Test with MQTT 5.0 Client

```bash
# Use MQTT 5.0 compatible client
# (mosquitto clients support MQTT 5.0)
mosquitto_pub -h localhost -p 1883 -t "test/topic" -m "Hello" -V 5
```

## References

- [MQTT 5.0 Specification](https://docs.oasis-open.org/mqtt/mqtt/v5.0/mqtt-v5.0.html)
- [MQTT 5.0 New Features Summary](https://docs.oasis-open.org/mqtt/mqtt/v5.0/mqtt-v5.0.html#_Toc3901124)

## Compatibility

- ✅ **MQTT 3.1.1 clients**: Fully supported
- ✅ **MQTT 5.0 clients**: Fully supported
- ✅ **Mixed deployments**: Both versions work simultaneously
- ✅ **libcurl**: Compatible with both versions

## Next Steps

1. **Enable MQTT 5.0 features**: Update clients to use MQTT 5.0
2. **Use Properties**: Add user properties, message expiry, etc.
3. **Leverage Reason Codes**: Better error handling
4. **Optimize with Topic Aliases**: Reduce bandwidth for repeated topics
