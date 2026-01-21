# MQTT over QUIC Implementation

## Overview

This document describes the MQTT over QUIC implementation, following patterns from curl's ngtcp2/nghttp3 reference code. QUIC provides lower latency connection setup and better performance in lossy networks.

## Architecture

### Transport Layer Abstraction

The implementation uses a single-stream mode where each MQTT connection maps to one QUIC bidirectional stream. This simplifies the implementation while providing QUIC benefits:

- **0-RTT/1-RTT connection setup**: Faster than TCP + TLS handshake
- **Better packet loss handling**: QUIC's congestion control is better than TCP in lossy networks
- **Connection migration**: QUIC can survive IP address changes
- **Multiplexing**: Multiple streams per connection (future enhancement)

### Reference Implementation

Based on curl's QUIC implementation in:
- `reference/curl/lib/vquic/vquic.c` - QUIC abstraction layer
- `reference/curl/lib/vquic/curl_ngtcp2.c` - ngtcp2 integration
- `reference/curl/lib/vquic/vquic_int.h` - Internal QUIC structures

### Python Implementation

Using `aioquic` library (Python port of QUIC):
- `mqttd/transport_quic.py` - QUIC transport implementation
- Single-stream mode for simplicity
- UDP-based transport (port 1884 by default)

## Usage

### Basic Server with QUIC

```python
from mqttd import MQTTApp

app = MQTTApp(
    port=1883,  # TCP port
    enable_quic=True,  # Enable QUIC
    quic_port=1884,  # UDP port for QUIC
    quic_certfile="cert.pem",  # TLS certificate (required)
    quic_keyfile="key.pem",  # TLS private key (required)
)

@app.subscribe("sensors/#")
async def handle_sensor(topic: str, client):
    print(f"Received on {topic}")

app.run()
```

### Generating TLS Certificates

QUIC requires TLS 1.3. Generate certificates:

```bash
# Generate self-signed certificate for testing
openssl req -x509 -newkey rsa:2048 -nodes \
    -keyout key.pem -out cert.pem -days 365 \
    -subj "/CN=localhost"
```

## Implementation Details

### QUIC Protocol Handler

The `MQTTQuicProtocol` class extends `QuicConnectionProtocol` from aioquic:

- Handles QUIC events (stream data, connection events)
- Maps QUIC streams to MQTT connections
- Provides reader/writer interface compatible with existing MQTT handlers

### Single-Stream Mode

Each MQTT connection uses one QUIC bidirectional stream:
- **Stream ID**: Unique identifier for the MQTT connection
- **Stream Buffer**: Buffers incoming MQTT packets
- **Stream Close**: Handles MQTT DISCONNECT

### TLS Requirements

QUIC requires TLS 1.3:
- Certificate and key files must be provided
- TLS handshake happens during QUIC connection establishment
- No separate TLS context needed (QUIC handles it)

## Testing with libcurl

Since the client uses libcurl with QUIC support:

```bash
# Connect via QUIC (curl needs to be built with ngtcp2/nghttp3)
curl --http3 \
    --cert cert.pem --key key.pem \
    mqtt://localhost:1884

# Note: libcurl's MQTT over QUIC support may vary
# Check curl version: curl --version | grep HTTP3
```

## Comparison: TCP vs QUIC

| Feature | TCP | QUIC |
|---------|-----|------|
| **Transport** | TCP/IP | UDP + QUIC |
| **TLS** | Optional (MQTTS) | Required (built-in) |
| **Handshake** | TCP (1-RTT) + TLS (1-RTT) = 2-RTT | QUIC + TLS (0-RTT/1-RTT) |
| **Port** | 1883 (MQTT), 8883 (MQTTS) | 1884 (custom) |
| **Latency** | Standard | Lower (especially 0-RTT) |
| **Packet Loss** | TCP retransmit | QUIC congestion control |
| **Connection Migration** | No | Yes |
| **Multiplexing** | No | Yes (streams) |

## Future Enhancements

1. **Multi-Stream Mode**: Map different MQTT topics to different QUIC streams
2. **0-RTT Connection Resumption**: Faster reconnections for returning clients
3. **Connection Migration**: Handle IP address changes seamlessly
4. **Unreliable Datagrams**: For QoS 0 messages (QUIC datagrams)

## Compatibility

### Clients

- **Standard MQTT clients**: Use TCP (port 1883)
- **QUIC-aware clients**: Use QUIC (port 1884) - Requires QUIC support
- **libcurl with QUIC**: Should work if curl is built with ngtcp2/nghttp3

### Network

- **Firewalls**: UDP port 1884 must be open (QUIC uses UDP)
- **NAT**: QUIC handles NAT traversal better than TCP
- **Load Balancers**: May need QUIC-aware load balancers

## References

- [curl QUIC Implementation](reference/curl/lib/vquic/)
- [MQTT over QUIC Specification](https://mqtt.ai/docs/mqtt-quic/)
- [aioquic Documentation](https://github.com/aiortc/aioquic)
- [EMQX MQTT over QUIC](https://www.emqx.com/en/blog/mqtt-over-quic)
