# Redis Pub/Sub Backend Integration

## Overview

The MQTTD server now uses **Redis pub/sub** as the message broker backend, providing:

- ✅ **Low Latency**: In-memory pub/sub, no database overhead
- ✅ **No Persistence**: Pure pub/sub - messages are not stored (optimized for real-time)
- ✅ **Scalable**: Redis handles message distribution efficiently
- ✅ **Automatic Routing**: Messages published to Redis are automatically forwarded to subscribed MQTT clients

## Architecture

### Message Flow

```
MQTT Client A (PUBLISH)  →  MQTT Server  →  Redis Channel  →  MQTT Server  →  MQTT Client B (SUBSCRIBE)
                                    ↓
                            Redis Pub/Sub
                                    ↓
                            (Broadcasts to all subscribers)
```

### How It Works

1. **Client Subscribes**:
   - MQTT client sends SUBSCRIBE for topic `sensors/temperature`
   - Server subscribes to Redis channel `sensors/temperature`
   - Server tracks the client subscription

2. **Client Publishes**:
   - MQTT client sends PUBLISH to topic `sensors/temperature`
   - Server publishes message to Redis channel `sensors/temperature`
   - Redis broadcasts to all subscribers of that channel

3. **Message Forwarding**:
   - Redis message listener receives message from Redis
   - Server forwards message to all subscribed MQTT clients
   - Clients receive the message via MQTT protocol

## Configuration

### Basic Setup

```python
from mqttd import MQTTApp

app = MQTTApp(
    port=1883,
    redis_host="localhost",
    redis_port=6379
)
```

### Advanced Configuration

```python
app = MQTTApp(
    port=1883,
    redis_host="redis.example.com",
    redis_port=6379,
    redis_db=0,
    redis_password="your_password"
)

# Or use Redis URL
app = MQTTApp(
    port=1883,
    redis_url="redis://:password@redis.example.com:6379/0"
)
```

## Features

### Automatic Topic Mapping

MQTT topics are directly mapped to Redis channels:
- MQTT topic: `sensors/temperature` → Redis channel: `sensors/temperature`
- MQTT topic: `devices/+/status` → Redis channel: `devices/+/status`

### Wildcard Support

MQTT wildcards are supported:
- `+` - Single-level wildcard
- `#` - Multi-level wildcard

When a message is published to Redis, it's matched against all subscription patterns.

### Low Latency

- **No Database**: Messages are not persisted, only routed
- **In-Memory**: Redis pub/sub is entirely in-memory
- **Async**: All operations are asynchronous for maximum performance

## Usage Example

```python
from mqttd import MQTTApp, MQTTMessage, MQTTClient

app = MQTTApp(port=1883, redis_host="localhost")

@app.subscribe("sensors/temperature")
async def handle_temp(topic: str, client: MQTTClient):
    print(f"Client {client.client_id} subscribed to {topic}")
    # Messages from Redis will be automatically forwarded

@app.publish_handler("sensors/+")
async def handle_publish(message: MQTTMessage, client: MQTTClient):
    print(f"Received: {message.topic} = {message.payload_str}")
    # Message is automatically published to Redis

app.run()
```

## Testing

### Start Redis

```bash
# Using Docker
docker run -d -p 6379:6379 redis:latest

# Or install locally
# Ubuntu/Debian: sudo apt-get install redis-server && sudo systemctl start redis
# macOS: brew install redis && brew services start redis
```

### Test with Multiple Clients

1. Start the server:
```bash
python3 examples/redis_server.py
```

2. In terminal 1, subscribe:
```bash
# Using mosquitto client
mosquitto_sub -h localhost -p 1883 -t "sensors/temperature"
```

3. In terminal 2, publish:
```bash
mosquitto_pub -h localhost -p 1883 -t "sensors/temperature" -m "25.5C"
```

4. Terminal 1 should receive the message!

## Performance Considerations

### Latency

- **Redis pub/sub**: Typically < 1ms for local Redis
- **Network**: Depends on network latency
- **MQTT protocol**: Minimal overhead

### Scalability

- Redis can handle millions of messages per second
- Multiple MQTT servers can share the same Redis instance
- Horizontal scaling: Run multiple MQTT servers, all connected to same Redis

### Memory Usage

- Redis pub/sub doesn't store messages (no persistence)
- Memory usage is minimal - only active subscriptions
- No message queue buildup

## Troubleshooting

### Redis Connection Failed

```
Error: Failed to connect to Redis: [Errno 111] Connection refused
```

**Solution**: Make sure Redis is running:
```bash
redis-cli ping
# Should return: PONG
```

### No Messages Received

1. Check Redis connection:
```python
import redis.asyncio as redis
r = redis.Redis(host='localhost', port=6379)
await r.ping()  # Should not raise exception
```

2. Check topic subscriptions:
   - Verify clients are subscribed
   - Check Redis channel names match MQTT topics

3. Enable debug logging:
```python
import logging
logging.basicConfig(level=logging.DEBUG)
```

## Comparison: With vs Without Redis

### Without Redis (Previous Version)
- Direct client-to-client routing
- Limited scalability
- No message distribution across multiple servers

### With Redis (Current Version)
- ✅ Centralized message routing
- ✅ Scalable across multiple servers
- ✅ Low latency pub/sub
- ✅ No database overhead
- ✅ Industry-standard message broker

## Future Enhancements

Potential improvements:
- Redis Streams support for message persistence (optional)
- Redis Cluster support for high availability
- Message filtering and transformation
- Metrics and monitoring integration
