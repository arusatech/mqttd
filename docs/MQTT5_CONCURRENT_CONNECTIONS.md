# MQTT 5.0 Concurrent Connections Handling

## Specification Requirements

According to the [MQTT 5.0 OASIS Specification](https://docs.oasis-open.org/mqtt/mqtt/v5.0/mqtt-v5.0.html), concurrent connections must be handled with proper session management:

### Key Requirements

1. **Client Identifier Uniqueness**: The server must handle cases where the same ClientID connects concurrently
2. **Session Management**: Sessions are tied to ClientID, not the connection socket
3. **Clean Start Behavior**: 
   - If Clean Start = 1: Discard existing session
   - If Clean Start = 0: Use existing session if Session Expiry Interval > 0
4. **Session Takeover**: When a new connection with same ClientID (Clean Start = 0), the old connection must be disconnected with Reason Code 0x8E (Session Taken Over)

### MQTT 5.0 Specification References

From Section 4.1 (CONNECT - Client requests connection to Server):

- **[MQTT-3.1.3-6]**: The Server MUST process a second CONNECT packet sent from a Client as a protocol violation and disconnect the Client.
- However, this applies per connection. For same ClientID on different connections:
  - If Clean Start = 1: New session replaces old
  - If Clean Start = 0: Old connection receives DISCONNECT with Reason Code 0x8E

From Section 3.1.2.11.2 (Session Expiry Interval):

- Session Expiry Interval controls how long the session persists after disconnect
- If Session Expiry Interval = 0: Session ends when Network Connection closes
- If Session Expiry Interval > 0: Session persists for that duration

## Current Implementation Analysis

### ✅ What Works

1. **Async Concurrency**: Uses `asyncio.start_server` which handles multiple concurrent connections
2. **Per-Connection Handling**: Each connection gets its own `_handle_client` task
3. **Connection Tracking**: Clients tracked by socket in `_clients` dictionary

### ❌ What's Missing

1. **ClientID-Based Session Management**: Currently tracks by socket, not ClientID
2. **Session Takeover Handling**: No logic to disconnect old connection when same ClientID reconnects
3. **Session Expiry**: No session persistence after disconnect
4. **Clean Start Logic**: Not properly implemented for MQTT 5.0
5. **Concurrent ClientID Conflict Resolution**: Same ClientID can connect multiple times without proper handling

## Required Enhancements

### 1. Session Management by ClientID

Track sessions by ClientID, not just by socket:

```python
# Current (socket-based)
self._clients: Dict[socket.socket, Tuple[MQTTClient, asyncio.StreamWriter]] = {}

# Needed (ClientID-based sessions)
self._sessions: Dict[str, Session] = {}  # ClientID -> Session
self._active_connections: Dict[str, socket.socket] = {}  # ClientID -> active socket
```

### 2. Session Takeover Detection

When a client connects with Clean Start = 0 and same ClientID:
- Check if another connection exists with same ClientID
- Send DISCONNECT (0x8E) to old connection
- Transfer session state to new connection

### 3. Session Expiry Tracking

- Track session expiry intervals
- Maintain session state (subscriptions, QoS 1/2 messages) after disconnect
- Clean up expired sessions

### 4. Concurrent Connection Limits

Implement connection limits per ClientID if needed.

## Implementation Plan

1. Create Session class to track session state
2. Implement ClientID-based session tracking
3. Add session takeover logic
4. Implement session expiry cleanup
5. Handle Clean Start flag properly
