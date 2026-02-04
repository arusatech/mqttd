# MQTTD - FastAPI-like MQTT/MQTTS Server

A high-performance Python package for creating MQTT and MQTTS servers with a FastAPI-like decorator-based API. Fully compatible with libcurl clients and designed for production use.

**Now supports MQTT 5.0** with full backward compatibility for MQTT 3.1.1.

---

## Supported Features (Code-Verified)

The following features are implemented and used in the codebase (`ref-code/mqttd`). This list is derived from line-by-line analysis of `mqttd/app.py`, `mqttd/session.py`, `mqttd/thread_safe.py`, and related modules.

### Core Features
- **FastAPI-like API**: Decorators `@app.subscribe(topic)` and `@app.publish_handler(topic)` for topic subscriptions and PUBLISH handlers
- **MQTT 5.0 Protocol**: Full support with automatic protocol detection (MQTT 3.1.1 vs 5.0)
- **MQTT 3.1.1 Compatibility**: Full backward compatibility
- **MQTTS Support**: TLS/SSL via `ssl_context` (e.g. port 8883)
- **QUIC Transport**: Optional MQTT over QUIC (ngtcp2, pure Python, or aioquic)
- **Async/Await**: Built on asyncio; one task per client connection
- **Configuration File**: `config_file` with options (version, PUBLISH-before-SUBACK, short-PUBLISH, error-CONNACK, excessive-remaining, Testnum)

### Multiple Concurrent Clients
- **Per-connection tasks**: Each TCP or QUIC connection is handled by a dedicated asyncio task (`_handle_client`)
- **Connection state**: `_clients` dict maps socket to `(MQTTClient, StreamWriter)`; connection limits via `max_connections` and `max_connections_per_ip`
- **Session management**: Per ClientID sessions (SessionManager); session takeover and concurrent same-ClientID handling per MQTT 5.0 (Clean Start, Session Present, Session Expiry Interval)

### MQTT 5.0 Features
- **Reason codes**: In CONNACK, SUBACK, UNSUBACK, PUBACK, etc.
- **Properties**: Full encode/decode for property types including User Properties, Message Expiry Interval, Topic Aliases, **Response Topic**, **Correlation Data**, Content Type, Subscription Identifier, Receive Maximum, etc.
- **Session**: Session Expiry Interval, Clean Start, Session Present, session takeover, expired session cleanup
- **Flow control**: Receive Maximum negotiation; in-flight QoS tracking per client
- **Will message**: Last Will and Testament with MQTT 5.0 properties; **Will Delay Interval** supported (delayed send after disconnect)
- **Subscription options**: No Local, Retain As Published, Retain Handling (0/1/2) per subscription
- **Topic aliases**: Server-side alias mapping per session
- **Message expiry**: Message Expiry Interval checked before forwarding

### Routing Modes
- **Direct routing** (default): In-memory routing; topic trie + shared subscription trie for O(m) lookup
- **Redis Pub/Sub** (optional): Publish to Redis channel by topic; subscribe to Redis when MQTT clients subscribe; `_redis_message_listener` forwards Redis messages to MQTT clients

### Redis and MCP Request/Response
- **Redis connection**: Optional `redis_host`/`redis_port`/`redis_url`; `_connect_redis()`, `_disconnect_redis()`, health check reports `redis_connected`
- **Redis Pub/Sub**: Publish on PUBLISH; subscribe per topic; forward Redis messages to subscribed MQTT clients
- **Store until MCP response, then reply to client**: When a client sends a PUBLISH with MQTT 5.0 **Response Topic** (and optionally **Correlation Data**), the server:
  - Stores request context in Redis at `mqttd:request:{correlation_id}` with TTL (e.g. 300s)
  - Publishes to channel `mqttd:mcp:requests` for MCP workers (payload: correlation_id, topic, payload_b64, response_topic)
  - Subscribes to `mqttd:mcp:responses`; when a response message arrives (JSON: correlation_id, payload_b64), looks up the stored request, forwards the reply to `response_topic`, and deletes the request key

### Retained Messages
- **Store/delete**: Retained PUBLISH stored in `_retained_messages`; empty payload with retain clears the topic
- **Delivery on subscribe**: `_deliver_retained_messages` with MQTT 5.0 retain_handling and retain_as_published

### Shared Subscriptions (MQTT 5.0)
- **Syntax**: `$share/group/topic`; round-robin delivery per group via `_shared_trie` and `_shared_group_index`

### Keepalive and Timeouts
- **Keepalive tracking**: `_client_keepalive` stores last_activity, keepalive_seconds, and optional ping task per socket
- **Activity reset**: On any received message (including PINGREQ and PUBLISH), last_activity is updated so timeout is effectively reset when the client is active
- **Keepalive monitor**: Background task disconnects if no activity for 1.5× keepalive interval
- **Read timeout**: `reader.read()` uses keepalive-based timeout (1.5× keepalive) so idle connections are closed

### Rate Limiting
- **Per-client**: `_rate_limits` tracks message count and subscription count per socket
- **Options**: `max_messages_per_second`, `max_subscriptions_per_minute`; `_check_rate_limit()` used on PUBLISH and SUBSCRIBE

### Observability and Admin
- **Metrics**: `get_metrics()` returns total_connections, current_connections, total_messages_published/received, total_subscriptions/unsubscriptions, retained_messages_count, active_subscriptions_count, connections_per_ip
- **Health**: `health_check()` returns status (healthy/degraded), running, connections, max_connections, redis_connected, errors list
- **Graceful shutdown**: `shutdown(timeout)` sets _running, closes server, waits for connections to drain (with timeout)

### Programmatic Publish
- **To all**: Normal PUBLISH routing and optional `app.publish(topic, payload, qos, retain)` when using Redis
- **To one client**: `publish_to_client(client, topic, payload, qos, retain)` sends a PUBLISH to a specific client by client_id

### Thread-Safety and No-GIL
- **Thread-safe structures** (`mqttd/thread_safe.py`): `ThreadSafeDict`, `ThreadSafeSet`, `ThreadSafeTopicTrie`, `ThreadSafeConnectionPool` (RLock-based) for use with Python 3.13+ no-GIL or 3.14t
- **Topic lookup**: `_topic_trie` and `_shared_trie` are ThreadSafeTopicTrie for O(m) subscription matching

### Transport
- **TCP**: `asyncio.start_server(_handle_client, host, port, ssl=...)`; can be disabled with `enable_tcp=False`
- **QUIC**: ngtcp2 (preferred), pure Python, or aioquic; `enable_quic`, `quic_port`, `quic_certfile`, `quic_keyfile`; QUIC-only mode when TCP disabled

---

## Installation

### Basic

```bash
pip install -e .
```

### Requirements
- **Python**: 3.7+ (3.13+ recommended for no-GIL; 3.14t for free-threaded)
- **Redis**: Optional — only for Redis pub/sub and MCP request/response (`pip install redis>=5.0.0` or `pip install -e ".[redis]"`)

### QUIC (ngtcp2 + WolfSSL)

```bash
./scripts/build-server.sh   # then pip install -e .
```

See [docs/BUILD_SERVER.md](docs/BUILD_SERVER.md) for details.

---

## Quick Start

### Basic server (direct routing, no Redis)

```python
from mqttd import MQTTApp, MQTTMessage, MQTTClient

app = MQTTApp(port=1883)

@app.subscribe("sensors/temperature")
async def on_subscribe(topic: str, client: MQTTClient):
    print(f"Client {client.client_id} subscribed to {topic}")

@app.publish_handler("sensors/+")
async def on_publish(message: MQTTMessage, client: MQTTClient):
    print(f"Received {message.topic}: {message.payload_str}")

if __name__ == "__main__":
    app.run()
```

### Multiple clients and connection limits

```python
app = MQTTApp(
    port=1883,
    max_connections=1000,
    max_connections_per_ip=50
)
# Each connection gets its own task; sessions are per ClientID
app.run()
```

### Redis Pub/Sub (multi-server)

```python
app = MQTTApp(
    port=1883,
    redis_host="localhost",
    redis_port=6379
)

@app.subscribe("sensors/#")
async def on_sub(topic: str, client: MQTTClient):
    print(f"{client.client_id} subscribed to {topic}")

@app.publish_handler("sensors/+")
async def on_pub(message: MQTTMessage, client: MQTTClient):
    print(f"PUBLISH {message.topic} (published to Redis)")

app.run()
```

### Redis + MCP request/response (store until agent replies)

Client sends PUBLISH with MQTT 5.0 **Response Topic** and optional **Correlation Data**. Server stores the request in Redis, publishes to `mqttd:mcp:requests`; when an MCP worker publishes a response to `mqttd:mcp:responses`, the server forwards the reply to the client’s response topic.

**Server (with Redis):**

```python
app = MQTTApp(port=1883, redis_host="localhost", redis_port=6379)

@app.subscribe("devices/+/request")
async def on_request_sub(topic: str, client: MQTTClient):
    print(f"Client {client.client_id} subscribed to {topic}")

@app.publish_handler("devices/+/request")
async def on_request_pub(message: MQTTMessage, client: MQTTClient):
    # Request is auto-stored in Redis (when response_topic is set) and
    # published to mqttd:mcp:requests; MCP workers consume and reply
    # to mqttd:mcp:responses; server then forwards to response_topic
    print(f"Request on {message.topic} from {client.client_id}")

app.run()
```

**MCP worker contract (Redis):**
- Subscribe to Redis channel `mqttd:mcp:requests`. Each message is JSON: `correlation_id`, `topic`, `payload_b64`, `response_topic`.
- After calling your MCP agent, publish to Redis channel `mqttd:mcp:responses` a JSON message: `{"correlation_id": "<id>", "payload_b64": "<base64 reply>"}`.

**Client (MQTT 5.0):** Publish with Response Topic and optional Correlation Data so the server stores the request and later delivers the reply on that topic.

### Metrics and health (e.g. for admin API)

```python
app = MQTTApp(port=1883)

# In another thread or admin endpoint:
metrics = app.get_metrics()
# total_connections, current_connections, total_messages_published,
# retained_messages_count, active_subscriptions_count, connections_per_ip, ...

health = app.health_check()
# status, running, connections, max_connections, redis_connected, errors
```

### MQTTS (TLS)

```python
import ssl
from mqttd import MQTTApp, MQTTClient

ssl_ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
ssl_ctx.load_cert_chain("server.crt", "server.key")

app = MQTTApp(port=8883, ssl_context=ssl_ctx)

@app.subscribe("secure/topic")
async def on_secure(topic: str, client: MQTTClient):
    print(f"Secure client: {client.client_id} -> {topic}")

app.run()
```

### MQTT over QUIC (QUIC-only)

```python
app = MQTTApp(
    enable_tcp=False,
    enable_quic=True,
    quic_port=1884,
    quic_certfile="cert.pem",
    quic_keyfile="key.pem",
)

@app.subscribe("sensors/#")
async def on_sensor(topic: str, client: MQTTClient):
    print(f"[{client.client_id}] Subscribed to {topic}")

app.run()
```

### Shared subscriptions (MQTT 5.0)

Clients subscribe with `$share/groupname/topic`. Server delivers each message to one member of the group (round-robin).

```python
app = MQTTApp(port=1883)

@app.subscribe("$share/workers/commands")
async def on_shared(topic: str, client: MQTTClient):
    print(f"Shared sub: {client.client_id} -> {topic}")

app.run()
```

### Configuration file

Create `mqttd.config`:

```
version 5
```

```python
app = MQTTApp(port=1883, config_file="mqttd.config")
app.run()
```

---

## Configuration Options

```python
MQTTApp(
    host="0.0.0.0",
    port=1883,
    ssl_context=None,
    config_file=None,
    redis_host=None,
    redis_port=6379,
    redis_db=0,
    redis_password=None,
    redis_url=None,
    use_redis=False,
    enable_tcp=True,
    enable_quic=False,
    quic_port=1884,
    quic_certfile=None,
    quic_keyfile=None,
    max_connections=None,
    max_connections_per_ip=None,
    max_messages_per_second=None,
    max_subscriptions_per_minute=None,
)
```

---

## API Reference (Summary)

- **`@app.subscribe(topic, qos=0)`** — Subscription handler; optional return bytes to send to subscriber.
- **`@app.publish_handler(topic=None)`** — PUBLISH handler; `topic` filter or all if `None`.
- **`app.run(host=None, port=None, ssl_context=None)`** — Blocking run.
- **`app.get_metrics()`** — Dict of server metrics.
- **`app.health_check()`** — Dict with status, running, connections, redis_connected, errors.
- **`app.shutdown(timeout=30.0)`** — Graceful shutdown (async).
- **`app.publish(topic, payload, qos=0, retain=False)`** — Programmatic publish (async; when Redis used).
- **`app.publish_to_client(client, topic, payload, qos=0, retain=False)`** — Send PUBLISH to one client (async).

**Types:** `MQTTMessage` (topic, payload, qos, retain, packet_id, payload_str, payload_json), `MQTTClient` (client_id, username, password, keepalive, clean_session, address).

---

## Architecture (Summary)

- **Multiple clients**: One asyncio task per connection; `_clients` dict; SessionManager per ClientID.
- **Routing**: Direct (in-memory trie) or Redis pub/sub; optional MCP flow via Redis keys/channels.
- **Thread-safety**: ThreadSafeTopicTrie (and related structures in `thread_safe.py`) for no-GIL readiness.
- **Protocols**: CONNECT/CONNACK, PUBLISH, PUBACK/PUBREC/PUBREL/PUBCOMP, SUBSCRIBE/SUBACK, UNSUBSCRIBE/UNSUBACK, PINGREQ/PINGRESP, DISCONNECT.

---

## Examples

See `examples/`:

- `basic_server.py` — Basic MQTT server
- `mqtt5_server.py` — MQTT 5.0
- `secure_server.py` — MQTTS
- `redis_server.py` — Redis pub/sub
- `direct_routing_server.py` — Direct routing
- `mqtt_quic_server.py` / `mqtt_quic_only_server.py` — QUIC
- `config_server.py` — Config file

---

## Testing

```bash
python tests/test_new_features.py
# or
pytest tests/ -v
```

---

## License

MIT License.

## Links

- **Repository**: https://github.com/arusatech/mqttd
- **Author**: Yakub Mohammad (yakub@arusatech.com)
- **Version**: 0.5.0 (see pyproject.toml)
