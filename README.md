# MQTTD - FastAPI-like MQTT/MQTTS Server

A Python package for creating MQTT and MQTTS servers with a FastAPI-like decorator-based API, compatible with libcurl clients.

**Now supports MQTT 5.0** with full backward compatibility for MQTT 3.1.1!

**Transport**: Standard TCP/IP (port 1883) or TLS/TCP (port 8883). QUIC/HTTP/3 support is not included.

## Features

- **FastAPI-like API**: Use decorators to define topic subscriptions and message handlers
- **MQTT 5.0 Protocol**: Full support for MQTT 5.0 with automatic protocol detection
- **MQTT 3.1.1 Compatibility**: Full backward compatibility with MQTT 3.1.1 clients
- **MQTT 5.0 Features**:
  - Reason codes in all ACK packets
  - Properties support (User Properties, Message Expiry, Topic Aliases, etc.)
  - Session Expiry Interval
  - Flow Control (Receive Maximum)
  - Maximum Packet Size negotiation
- **Two Routing Modes**:
  - **Direct Routing** (default): In-memory routing between clients (lower latency, single server)
  - **Redis Pub/Sub** (optional): Distributed routing for multi-server deployments
- **MQTTS Support**: TLS/SSL support for secure MQTT connections
- **Async/Await**: Built on asyncio for high-performance async operations
- **Configuration File**: Support for configuration files (similar to C reference implementation)

## Installation

```bash
pip install -e .
```

**Requirements:**
- Python 3.7+
- Redis server (optional - only needed if using Redis pub/sub mode)

**Redis is optional!** The server works without Redis using direct routing (default).

## Quick Start

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

### MQTTS (TLS) Server with Redis

```python
import ssl
from mqttd import MQTTApp

# Create SSL context
ssl_context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
ssl_context.load_cert_chain('server.crt', 'server.key')

# MQTTS with Redis backend
app = MQTTApp(
    port=8883,
    ssl_context=ssl_context,
    redis_host="localhost",
    redis_port=6379
)

@app.subscribe("secure/topic")
async def handle_secure(topic: str, client: MQTTClient):
    print(f"Secure client subscribed: {topic}")

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

## Configuration Options

The configuration file supports the following options (similar to C reference):

- `version` - MQTT protocol version (default: 5 for MQTT 3.1.1)
- `PUBLISH-before-SUBACK` - Send PUBLISH before SUBACK (for testing)
- `short-PUBLISH` - Send truncated PUBLISH messages (for error testing)
- `error-CONNACK` - Set CONNACK return code (0=accepted, 5=not authorized, etc.)
- `excessive-remaining` - Send invalid remaining length (for protocol error testing)
- `Testnum` - Test number for loading test-specific data

## API Reference

### MQTTApp

Main application class for creating MQTT servers with Redis pub/sub backend.

#### Initialization Parameters

- `host` - MQTT server host (default: "0.0.0.0")
- `port` - MQTT server port (default: 1883)
- `ssl_context` - SSL context for MQTTS (optional)
- `config_file` - Path to configuration file (optional)
- `redis_host` - Redis server host (default: "localhost")
- `redis_port` - Redis server port (default: 6379)
- `redis_db` - Redis database number (default: 0)
- `redis_password` - Redis password (optional)
- `redis_url` - Redis connection URL (overrides host/port/db/password if provided)

#### Methods

- `subscribe(topic: str, qos: int = 0)` - Decorator for topic subscriptions
- `publish_handler(topic: Optional[str] = None)` - Decorator for PUBLISH message handlers
- `run(host: Optional[str] = None, port: Optional[int] = None, ssl_context: Optional[ssl.SSLContext] = None)` - Run the server
- `publish(topic: str, payload: bytes, qos: int = 0, retain: bool = False)` - Publish message via Redis

### Types

- `MQTTMessage` - Represents an MQTT message with topic, payload, QoS, etc.
- `MQTTClient` - Represents a connected MQTT client

## Testing with libcurl

The server is compatible with libcurl's MQTT implementation. Example curl command:

```bash
# Subscribe
curl --mqtt-pub "sensors/temp" --data "25.5" mqtt://localhost:1883

# Publish
curl --mqtt-sub "sensors/temp" mqtt://localhost:1883
```

## Architecture

### Two Routing Modes

#### 1. Direct Routing (Default - No Redis)

**Message Flow:**
```
Client A (PUBLISH) → Server → Direct lookup → Client B, C, D (receive)
```

- **Lower latency**: No Redis network hop
- **Simpler**: No external dependencies
- **Single server**: All clients must connect to the same server
- **In-memory**: Direct routing within the server process

#### 2. Redis Pub/Sub (Optional - For Scaling)

**Message Flow:**
```
Client A (PUBLISH) → Server → Redis Channel → Redis broadcasts → Server → Client B, C, D
```

- **Scalable**: Multiple servers can share the same Redis
- **Distributed**: Messages flow across server boundaries
- **High availability**: If one server dies, others continue
- **Slightly higher latency**: One extra network hop to Redis

**When to use each:**
- **Direct Routing**: Single server, maximum performance, simplicity
- **Redis Pub/Sub**: Multiple servers, horizontal scaling, high availability

## Protocol Support

The server implements the following MQTT message types:

- CONNECT / CONNACK
- PUBLISH
- SUBSCRIBE / SUBACK
- DISCONNECT

## License

MIT License

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.
