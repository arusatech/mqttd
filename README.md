# MQTTD - FastAPI-like MQTT/MQTTS Server

A high-performance Python package for creating MQTT and MQTTS servers with a FastAPI-like decorator-based API. Fully compatible with libcurl clients and designed for production use.

**Now supports MQTT 5.0** with full backward compatibility for MQTT 3.1.1!

## 🚀 Features

### Core Features
- **FastAPI-like API**: Use decorators to define topic subscriptions and message handlers
- **MQTT 5.0 Protocol**: Full support for MQTT 5.0 with automatic protocol detection
- **MQTT 3.1.1 Compatibility**: Full backward compatibility with MQTT 3.1.1 clients
- **MQTTS Support**: TLS/SSL support for secure MQTT connections (port 8883)
- **QUIC/HTTP3 Support**: Optional QUIC transport for lower latency and better performance in lossy networks
- **Async/Await**: Built on asyncio for high-performance async operations
- **Configuration File**: Support for configuration files (similar to C reference implementation)

### MQTT 5.0 Features
- **Reason Codes**: Reason codes in all ACK packets
- **Properties Support**: Full support for all 32 property types including:
  - User Properties
  - Message Expiry Interval
  - Topic Aliases
  - Response Topic
  - Correlation Data
  - Content Type
  - And many more...
- **Session Management**: 
  - Session Expiry Interval
  - Clean Start flag
  - Session Present indicator
  - Proper session takeover handling
- **Flow Control**: Receive Maximum negotiation
- **Packet Size Limits**: Maximum Packet Size negotiation
- **Will Message**: Last Will and Testament with MQTT 5.0 properties

### Routing Modes
- **Direct Routing** (default): In-memory routing between clients (lower latency, single server)
- **Redis Pub/Sub** (optional): Distributed routing for multi-server deployments

### Performance & Scalability
- **No-GIL Support**: Compatible with Python 3.13+ no-GIL mode for true parallelism
- **Thread-Safe**: Thread-safe topic trie for O(m) subscription lookup (m = topic depth)
- **Connection Limits**: Configurable connection limits and rate limiting
- **Session Persistence**: Efficient session management with expiry support

### Transport Protocols
- **TCP/IP**: Standard MQTT over TCP (port 1883) - Default, can be disabled
- **TLS/TCP**: Secure MQTT over TLS (port 8883)
- **QUIC/HTTP3**: Optional QUIC transport with multiple implementations:
  - **ngtcp2** (production-grade, best performance) - requires C library
  - **Pure Python** (compatible with no-GIL Python)
  - **aioquic** (fallback for regular Python)
- **Transport Modes**:
  - **TCP-only** (default): `enable_tcp=True, enable_quic=False`
  - **QUIC-only**: `enable_tcp=False, enable_quic=True`
  - **Both**: `enable_tcp=True, enable_quic=True` (parallel operation)

## 📦 Installation

### Basic Installation

```bash
pip install -e .
```

### Requirements
- **Python**: 3.13+ (recommended for no-GIL support) or 3.7+ (standard Python)
- **Redis**: Optional - only needed if using Redis pub/sub mode (default: direct routing, no Redis needed)

**Redis is optional!** The server works without Redis using direct routing (default).

### Optional Dependencies

For QUIC support with ngtcp2 (production-grade):
```bash
# Install ngtcp2 C library (system package)
# See: https://github.com/ngtcp2/ngtcp2
# Then install Python bindings if available
```

For development:
```bash
pip install -e ".[dev]"
```

## 🎯 Quick Start

### Basic MQTT Server (Direct Routing - No Redis)

```python
from mqttd import MQTTApp, MQTTMessage, MQTTClient

# Create app with direct routing (default - no Redis needed!)
app = MQTTApp(port=1883)  # use_redis=False by default

@app.subscribe("sensors/temperature")
async def handle_temperature(topic: str, client: MQTTClient):
    """Handle subscription to temperature topic"""
    print(f"Client {client.client_id} subscribed to {topic}")
    # Messages will be directly routed to this client

@app.publish_handler("sensors/+")
async def handle_publish(message: MQTTMessage, client: MQTTClient):
    """Handle incoming PUBLISH messages - directly routed to subscribers"""
    print(f"Received on {message.topic}: {message.payload_str}")
    # Message is automatically routed directly to subscribed clients

if __name__ == "__main__":
    app.run()
```

**How it works (Direct Routing):**
- When a client **subscribes** to a topic, the server tracks the subscription in memory
- When a client **publishes** a message, the server directly sends it to all subscribed clients
- **Lower latency** - no Redis network hop
- **Simpler** - no external dependencies
- **Perfect for single-server deployments**

### MQTT Server with Redis (Multi-Server)

```python
from mqttd import MQTTApp, MQTTMessage, MQTTClient

# Create app with Redis pub/sub backend (for multi-server scaling)
app = MQTTApp(
    port=1883,
    redis_host="localhost",  # Enable Redis mode
    redis_port=6379
)

@app.subscribe("sensors/temperature")
async def handle_temperature(topic: str, client: MQTTClient):
    """Handle subscription to temperature topic"""
    print(f"Client {client.client_id} subscribed to {topic}")
    # Messages from Redis will be automatically forwarded to this client

@app.publish_handler("sensors/+")
async def handle_publish(message: MQTTMessage, client: MQTTClient):
    """Handle incoming PUBLISH messages - automatically published to Redis"""
    print(f"Received on {message.topic}: {message.payload_str}")
    # Message is automatically published to Redis channel

if __name__ == "__main__":
    app.run()
```

**How it works (Redis Mode):**
- When a client **subscribes** to a topic, the server subscribes to the corresponding Redis channel
- When a client **publishes** a message, it's published to Redis
- Redis messages are automatically forwarded to all subscribed MQTT clients
- **Scalable** - multiple servers can share the same Redis
- **Distributed** - messages flow across server boundaries

### MQTTS (TLS) Server

```python
import ssl
from mqttd import MQTTApp, MQTTMessage, MQTTClient

# Create SSL context
ssl_context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
ssl_context.load_cert_chain('server.crt', 'server.key')

# MQTTS with direct routing
app = MQTTApp(
    port=8883,
    ssl_context=ssl_context
)

@app.subscribe("secure/topic")
async def handle_secure(topic: str, client: MQTTClient):
    print(f"Secure client subscribed: {topic}")

app.run()
```

### MQTT 5.0 Server

```python
from mqttd import MQTTApp, MQTTMessage, MQTTClient

# Create MQTT app (automatically handles both MQTT 3.1.1 and 5.0)
app = MQTTApp(port=1883)

@app.subscribe("sensors/temperature")
async def handle_temperature_subscribe(topic: str, client: MQTTClient):
    """Handle subscription - works with both MQTT 3.1.1 and 5.0"""
    protocol_version = getattr(client, '_protocol_version', 4)
    mqtt_version = "MQTT 5.0" if protocol_version == 5 else "MQTT 3.1.1"
    print(f"Client {client.client_id} ({mqtt_version}) subscribed to {topic}")

@app.publish_handler("sensors/+")
async def handle_sensor_publish(message: MQTTMessage, client: MQTTClient):
    """Handle incoming PUBLISH messages"""
    print(f"Received PUBLISH from {client.client_id}")
    print(f"  Topic: {message.topic}")
    print(f"  Payload: {message.payload_str}")
    print(f"  QoS: {message.qos}")

if __name__ == "__main__":
    app.run()
```

### MQTT over QUIC Server (Parallel Mode - TCP + QUIC)

```python
from mqttd import MQTTApp, MQTTMessage, MQTTClient

# Create MQTT app with both TCP and QUIC enabled
app = MQTTApp(
    port=1883,  # TCP port
    enable_tcp=True,   # Enable TCP transport (default)
    enable_quic=True,  # Enable QUIC transport
    quic_port=1884,   # UDP port for QUIC
    quic_certfile="cert.pem",  # TLS certificate (required for QUIC)
    quic_keyfile="key.pem",    # TLS private key (required for QUIC)
)

@app.subscribe("sensors/#")
async def handle_sensor(topic: str, client: MQTTClient):
    """Handle sensor messages"""
    print(f"[{client.client_id}] Subscribed to {topic}")

@app.publish_handler("sensors/temperature")
async def handle_temperature(message: MQTTMessage, client: MQTTClient):
    """Handle temperature publishes"""
    print(f"Temperature from {client.client_id}: {message.payload_str}")

if __name__ == "__main__":
    print("Starting MQTT server with both TCP and QUIC...")
    print("TCP: mqtt://localhost:1883")
    print("QUIC: quic://localhost:1884")
    app.run()
```

### MQTT over QUIC Server (QUIC-Only Mode)

```python
from mqttd import MQTTApp, MQTTMessage, MQTTClient

# Create MQTT app with QUIC-only mode (TCP disabled)
app = MQTTApp(
    enable_tcp=False,  # Disable TCP transport
    enable_quic=True,   # Enable QUIC transport (ngtcp2)
    quic_port=1884,    # UDP port for QUIC
    quic_certfile="cert.pem",  # TLS certificate (required for QUIC)
    quic_keyfile="key.pem",    # TLS private key (required for QUIC)
)

@app.subscribe("sensors/#")
async def handle_sensor(topic: str, client: MQTTClient):
    """Handle sensor messages"""
    print(f"[{client.client_id}] Subscribed to {topic}")

@app.publish_handler("sensors/temperature")
async def handle_temperature(message: MQTTMessage, client: MQTTClient):
    """Handle temperature publishes"""
    print(f"Temperature from {client.client_id}: {message.payload_str}")

if __name__ == "__main__":
    print("Starting MQTT server in QUIC-only mode...")
    print("QUIC: quic://localhost:1884")
    print("Note: TCP connections are disabled")
    app.run()
```

### With Configuration File

Create a `mqttd.config` file:

```
version 5
Testnum 1190
```

Then use it:

```python
app = MQTTApp(port=1883, config_file="mqttd.config")
app.run()
```

## ⚙️ Configuration Options

### MQTTApp Initialization Parameters

```python
MQTTApp(
    host="0.0.0.0",                    # Host to bind to
    port=1883,                         # Port to listen on
    ssl_context=None,                  # SSL context for MQTTS (optional)
    config_file=None,                  # Path to configuration file (optional)
    
    # Redis Configuration (optional)
    redis_host=None,                   # Redis server host (None = no Redis)
    redis_port=6379,                   # Redis server port
    redis_db=0,                        # Redis database number
    redis_password=None,               # Redis password (optional)
    redis_url=None,                    # Redis connection URL (overrides above)
    use_redis=False,                   # Enable Redis pub/sub backend
    
    # Transport Configuration
    enable_tcp=True,                    # Enable TCP transport (default: True)
    enable_quic=False,                 # Enable QUIC/HTTP3 transport (default: False)
    quic_port=1884,                    # UDP port for QUIC server
    quic_certfile=None,                # Path to TLS certificate for QUIC
    quic_keyfile=None,                 # Path to TLS private key for QUIC
    
    # Connection Limits (optional)
    max_connections=None,              # Maximum total connections (None = unlimited)
    max_connections_per_ip=None,      # Maximum connections per IP address
    max_messages_per_second=None,     # Rate limit for messages per second per client
    max_subscriptions_per_minute=None # Rate limit for subscriptions per minute per client
)
```

### Configuration File Options

The configuration file supports the following options (similar to C reference):

- `version` - MQTT protocol version (default: 5 for MQTT 5.0, 4 for MQTT 3.1.1)
- `PUBLISH-before-SUBACK` - Send PUBLISH before SUBACK (for testing)
- `short-PUBLISH` - Send truncated PUBLISH messages (for error testing)
- `error-CONNACK` - Set CONNACK return code (0=accepted, 5=not authorized, etc.)
- `excessive-remaining` - Send invalid remaining length (for protocol error testing)
- `Testnum` - Test number for loading test-specific data

## 📚 API Reference

### MQTTApp

Main application class for creating MQTT servers.

#### Methods

##### `subscribe(topic: str, qos: int = 0)`

Decorator for topic subscriptions.

**Parameters:**
- `topic`: MQTT topic pattern (supports wildcards: `+` for single level, `#` for multi-level)
- `qos`: Quality of Service level (0, 1, or 2)

**Example:**
```python
@app.subscribe("sensors/temperature", qos=1)
async def handle_temp(topic: str, client: MQTTClient):
    print(f"Subscribed to {topic}")
    # Optional: return bytes to send to subscribing client
    return b"Welcome message"
```

##### `publish_handler(topic: Optional[str] = None)`

Decorator for PUBLISH message handlers.

**Parameters:**
- `topic`: Optional topic filter. If `None`, handles all PUBLISH messages.

**Example:**
```python
@app.publish_handler("sensors/+")
async def handle_publish(message: MQTTMessage, client: MQTTClient):
    print(f"Received: {message.topic} = {message.payload_str}")
```

##### `run(host: Optional[str] = None, port: Optional[int] = None, ssl_context: Optional[ssl.SSLContext] = None)`

Run the server (blocking call).

**Parameters:**
- `host`: Override host (default: uses initialization value)
- `port`: Override port (default: uses initialization value)
- `ssl_context`: Override SSL context (default: uses initialization value)

##### `async publish(topic: str, payload: bytes, qos: int = 0, retain: bool = False)`

Publish message programmatically (when using Redis mode).

**Parameters:**
- `topic`: Topic to publish to
- `payload`: Message payload (bytes)
- `qos`: Quality of Service level (0, 1, or 2)
- `retain`: Retain flag

### Types

#### `MQTTMessage`

Represents an MQTT message.

**Attributes:**
- `topic: str` - Message topic
- `payload: bytes` - Message payload
- `qos: int` - Quality of Service level
- `retain: bool` - Retain flag
- `packet_id: Optional[int]` - Packet ID (for QoS > 0)

**Properties:**
- `payload_str: str` - Payload as UTF-8 string
- `payload_json: Any` - Payload parsed as JSON

#### `MQTTClient`

Represents a connected MQTT client.

**Attributes:**
- `client_id: str` - Client identifier
- `username: Optional[str]` - Username
- `password: Optional[str]` - Password
- `keepalive: int` - Keepalive interval in seconds
- `clean_session: bool` - Clean session flag
- `address: Optional[tuple]` - Client address (host, port)

#### `QoS`

Quality of Service enumeration:
- `QoS.AT_MOST_ONCE = 0`
- `QoS.AT_LEAST_ONCE = 1`
- `QoS.EXACTLY_ONCE = 2`

## 🏗️ Architecture

### Two Routing Modes

#### 1. Direct Routing (Default - No Redis)

**Message Flow:**
```
Client A (PUBLISH) → Server → Direct lookup → Client B, C, D (receive)
```

**Characteristics:**
- **Lower latency**: No Redis network hop
- **Simpler**: No external dependencies
- **Single server**: All clients must connect to the same server
- **In-memory**: Direct routing within the server process
- **Thread-safe**: Uses thread-safe topic trie for O(m) subscription lookup

#### 2. Redis Pub/Sub (Optional - For Scaling)

**Message Flow:**
```
Client A (PUBLISH) → Server → Redis Channel → Redis broadcasts → Server → Client B, C, D
```

**Characteristics:**
- **Scalable**: Multiple servers can share the same Redis
- **Distributed**: Messages flow across server boundaries
- **High availability**: If one server dies, others continue
- **Slightly higher latency**: One extra network hop to Redis

**When to use each:**
- **Direct Routing**: Single server, maximum performance, simplicity
- **Redis Pub/Sub**: Multiple servers, horizontal scaling, high availability

### Protocol Support

The server implements the following MQTT message types:

- **CONNECT / CONNACK** - Client connection
- **PUBLISH** - Message publishing
- **PUBACK / PUBREC / PUBREL / PUBCOMP** - QoS 1 and 2 acknowledgments
- **SUBSCRIBE / SUBACK** - Topic subscriptions
- **UNSUBSCRIBE / UNSUBACK** - Unsubscribe from topics
- **PINGREQ / PINGRESP** - Keepalive
- **DISCONNECT** - Client disconnection

### MQTT 5.0 Features

- **Automatic Protocol Detection**: Handles both MQTT 3.1.1 and 5.0 clients
- **Properties System**: Full encoding/decoding for all 32 property types
- **Reason Codes**: All reason codes for all packet types
- **Session Management**: Proper session handling with expiry and takeover
- **Flow Control**: Receive Maximum and Maximum Packet Size negotiation

### No-GIL Support

The package is compatible with Python 3.13+ no-GIL mode (`--disable-gil` flag) for true parallelism:

- **True Parallelism**: Multiple threads can execute Python code simultaneously
- **Better CPU Utilization**: All CPU cores can be used efficiently
- **Simpler Architecture**: Single process instead of multi-process
- **Lower Memory Overhead**: Shared memory instead of process duplication

## 🧪 Testing

### Running Tests

```bash
# Run basic tests
python tests/test_basic.py

# Run all tests
pytest tests/

# Run with verbose output
pytest tests/ -v
```

### Testing with libcurl

The server is compatible with libcurl's MQTT implementation:

```bash
# Publish a message
curl --mqtt-pub "sensors/temp" --data "25.5" mqtt://localhost:1883

# Subscribe to a topic
curl --mqtt-sub "sensors/temp" mqtt://localhost:1883
```

## 📖 Examples

See the `examples/` directory for complete examples:

- `basic_server.py` - Basic MQTT server
- `mqtt5_server.py` - MQTT 5.0 server
- `secure_server.py` - MQTTS (TLS) server
- `redis_server.py` - Redis pub/sub backend
- `direct_routing_server.py` - Direct routing mode
- `mqtt_quic_server.py` - QUIC transport
- `config_server.py` - Configuration file usage

## 🔧 Development

### Project Structure

```
mqttd/
├── __init__.py              # Package exports
├── app.py                   # Main MQTTApp class
├── protocol.py              # MQTT 3.1.1 protocol
├── protocol_v5.py           # MQTT 5.0 protocol
├── properties.py            # MQTT 5.0 properties encoding/decoding
├── reason_codes.py          # MQTT 5.0 reason codes
├── session.py               # Session management
├── types.py                 # Type definitions
├── decorators.py            # FastAPI-like decorators
├── thread_safe.py           # Thread-safe data structures
├── transport_quic.py        # QUIC transport (aioquic)
├── transport_quic_pure.py   # Pure Python QUIC
├── transport_quic_ngtcp2.py # ngtcp2 QUIC bindings
├── ngtcp2_bindings.py       # ngtcp2 C bindings
└── ngtcp2_tls_bindings.py   # ngtcp2 TLS bindings

examples/                    # Example servers
tests/                       # Test suite
docs/                        # Documentation
```

### Building from Source

```bash
git clone https://github.com/arusatech/mqttd.git
cd mqttd
pip install -e .
```

## 📄 License

MIT License

## 🤝 Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## 🔗 Links

- **Repository**: https://github.com/arusatech/mqttd
- **Author**: Yakub Mohammad (yakub@arusatech.com)
- **Version**: 0.2.0

## 📝 Changelog

### Version 0.2.0
- Added MQTT 5.0 support with full backward compatibility
- Added QUIC/HTTP3 transport support
- Added session management with expiry
- Added connection limits and rate limiting
- Improved thread-safety with thread-safe topic trie
- Added no-GIL Python compatibility

### Version 0.1.0
- Initial release
- MQTT 3.1.1 support
- FastAPI-like decorator API
- Redis pub/sub backend
- TLS/SSL support
