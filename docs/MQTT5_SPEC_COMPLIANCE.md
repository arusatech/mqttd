# MQTT v5.0 Specification Compliance Checklist

Reference: [OASIS MQTT v5.0](https://docs.oasis-open.org/mqtt/mqtt/v5.0/mqtt-v5.0.html). Server conformance: §7.1.1 MQTT Server conformance clause.

## Control Packets (§2.1.2)

| Packet     | Spec | Status | Notes |
|-----------|------|--------|-------|
| CONNECT   | 1    | ✅     |       |
| CONNACK   | 2    | ✅     |       |
| PUBLISH   | 3    | ✅     |       |
| PUBACK    | 4    | ✅     |       |
| PUBREC    | 5    | ✅     |       |
| PUBREL    | 6    | ✅     |       |
| PUBCOMP   | 7    | ✅     |       |
| SUBSCRIBE | 8    | ✅     |       |
| SUBACK    | 9    | ✅     |       |
| UNSUBSCRIBE | 10 | ✅     |       |
| UNSUBACK  | 11   | ✅     |       |
| PINGREQ   | 12   | ✅     |       |
| PINGRESP  | 13   | ✅     |       |
| DISCONNECT| 14   | ✅     |       |
| **AUTH**  | **15** | ✅   | Packet type 15; CONNECT→AUTH→CONNACK flow and re-auth (§4.12.1) |

## CONNECT / CONNACK (§3.1, §3.2)

| Feature | Status | Notes |
|---------|--------|-------|
| Protocol name "MQTT", version 5 | ✅ | |
| Connect flags (Clean Start, Will, User/Password) | ✅ | |
| CONNECT properties (all 32 types) | ✅ | Encoded/decoded in `properties.py`, `protocol_v5.py` |
| Zero-length ClientID → Assigned Client Identifier | ✅ | Server assigns unique ID and returns in CONNACK |
| Will message + Will properties | ✅ | |
| Will Delay Interval (scheduling/cancel on reconnect) | ✅ | Delay + cancel on reconnect; `_pending_will_tasks`, `_send_will_after_delay` |
| Session Expiry Interval, Clean Start | ✅ | Used in session manager |

## PUBLISH (§3.3)

| Feature | Status | Notes |
|---------|--------|-------|
| Topic, QoS, RETAIN, properties | ✅ | |
| Message Expiry Interval | ✅ | Applied when forwarding |
| Topic Alias | ✅ | Per-connection alias storage and use |
| Response Topic, Correlation Data, User Property | ✅ | Forwarded |
| Subscription Identifier(s) on server→client PUBLISH | ✅ | |

## SUBSCRIBE / SUBACK (§3.8, §3.9)

| Feature | Status | Notes |
|---------|--------|-------|
| Packet ID, properties, Subscription Identifier | ✅ | |
| **Multiple topic filters in one SUBSCRIBE** | ✅ | `parse_subscribe_v5` returns `filters` list; app iterates and SUBACK per filter |
| **Subscription Options byte** | ✅ | No Local, Retain As Published, Retain Handling parsed and applied when forwarding |
| SUBACK reason code per topic filter | ✅ | One reason code per filter; No Local on shared rejected with failure code |

## Shared Subscriptions (§4.8.2)

| Feature | Status | Notes |
|---------|--------|-------|
| `$share/{ShareName}/{filter}` | ✅ | Supported; CONNACK sends `shared_subscription_available=1`; one delivery per group (round-robin) |

## UNSUBSCRIBE / UNSUBACK (§3.10, §3.11)

| Feature | Status | Notes |
|---------|--------|-------|
| Multiple topic filters | ✅ | Parser returns list; UNSUBACK with multiple reason codes |

## DISCONNECT (§3.14)

| Feature | Status | Notes |
|---------|--------|-------|
| Server sends DISCONNECT with reason code | ✅ | e.g. Session Taken Over (0x8E) |
| DISCONNECT properties | ✅ | Builder; server must not send Session Expiry Interval |

## AUTH – Enhanced Authentication (§3.15, §4.12)

| Feature | Status | Notes |
|---------|--------|-------|
| AUTH packet type and exchange | ✅ | `MQTTMessageType.AUTH`; build/parse in `protocol_v5`; CONNECT→AUTH→CONNACK and re-auth in `app.py` |
| CONNECT/CONNACK Auth Method/Data | ✅ | Properties; challenge/response flow enforced until CONNACK |

### Understanding AUTH from the spec (§3.15, §4.12)

**What AUTH is**

- **AUTH** is MQTT v5.0 Control Packet type **15** (Spec Table 2-1). It is used only for **enhanced (challenge/response) authentication**.
- The spec: *"An AUTH packet is sent from Client to Server or Server to Client as part of an extended authentication exchange, such as challenge / response authentication."*
- Sending AUTH when CONNECT did **not** include the same **Authentication Method** is a **Protocol Error** (§3.15).

**When AUTH is used**

- **Optional.** If the client does **not** include **Authentication Method** in CONNECT, the server must not send AUTH; the client must not send AUTH. Normal (e.g. username/password) auth can still apply.
- **When CONNECT has Authentication Method:** the client requests enhanced auth. The server may send **AUTH** with reason code **0x18 (Continue authentication)** and optional **Authentication Data**. The client responds with AUTH (0x18) and optional data. The exchange continues until the server sends **CONNACK** with **0x00 (Success)** or rejects (CONNACK reason ≥ 0x80) and closes.
- **Rule:** If the client sent Authentication Method in CONNECT, the client **must not** send any packet other than **AUTH** or **DISCONNECT** until it has received CONNACK (§3.1.4).

**AUTH packet format (§3.15)**

- **Fixed header:** Packet type 15; reserved bits 0.
- **Variable header:**
  - **Authenticate Reason Code** (1 byte): **0x00** Success (server); **0x18** Continue authentication (client or server); **0x19** Re-authenticate (client only, after connection).
  - **Properties:** Property Length, then optional Authentication Method (0x15), Authentication Data (0x16), Reason String (0x1F), User Property (0x26). All AUTH packets and a successful CONNACK in that exchange must use the **same** Authentication Method as in CONNECT.
- **Payload:** None.

**Initial authentication flow (§4.12)**

1. Client sends **CONNECT** with **Authentication Method** (e.g. `"SCRAM-SHA-1"`) and optionally **Authentication Data**.
2. If the server does not support it: CONNACK **0x8C** or **0x87** and close.
3. If the server needs more data: **AUTH** with **0x18** and optional Authentication Data.
4. Client sends **AUTH** with **0x18** and optional Authentication Data; repeat 3–4 as the mechanism requires.
5. Server ends with **CONNACK 0x00** (success) or CONNACK ≥ 0x80 and close (failure).

**Re-authentication (§4.12.1)**

- After CONNACK, the client may send **AUTH** with reason **0x19 (Re-authenticate)** and the same Authentication Method to start re-auth. The server responds with AUTH **0x00** (Success) or **0x18** (Continue). Other traffic may continue during re-auth.

**What mqttd implements**

- CONNECT and CONNACK carry **Authentication Method** and **Authentication Data**; AUTH packet type 15 in `MQTTMessageType`; `build_auth_v5` / `parse_auth_v5` in `protocol_v5.py`; CONNECT→AUTH→…→CONNACK flow in `app.py` with enforcement that the client sends only AUTH or DISCONNECT until CONNACK; re-authentication (AUTH 0x19) after connection.

## Request/Response (§4.10)

| Feature | Status | Notes |
|---------|--------|-------|
| Response Topic, Correlation Data on PUBLISH | ✅ | Forwarded |
| Request/Response Information in CONNECT/CONNACK | ✅ | In builders |

## Topic Names and Filters (§4.7)

| Feature | Status | Notes |
|---------|--------|-------|
| Wildcards `+`, `#` | ✅ | `decorators.topic_matches` |
| **Topics starting with `$`** | ✅ | §4.7.2: `topic_matches` in `decorators.py` returns False when topic starts with `$` and pattern starts with `#` or `+` |

## Session (§4.1)

| Feature | Status | Notes |
|---------|--------|-------|
| Session state, Clean Start, Session Expiry | ✅ | `session.py`, `SessionManager` |
| Session Present in CONNACK | ✅ | |
| Session takeover (DISCONNECT 0x8E) | ✅ | |

## Flow Control (§4.9)

| Feature | Status | Notes |
|---------|--------|-------|
| Receive Maximum | ✅ | Enforced when sending QoS>0 to client |

## Reason Codes & Properties

| Feature | Status | Notes |
|---------|--------|-------|
| Reason codes in CONNACK, PUBACK, PUBREC, PUBREL, PUBCOMP, SUBACK, UNSUBACK, DISCONNECT | ✅ | `reason_codes.py` |
| All 32 property types (encode/decode) | ✅ | `properties.py`, `protocol_v5.py` |

---

## Summary

- **Implemented:** All control packets including AUTH (15); CONNECT/CONNACK with zero-length ClientID and Assigned Client Identifier; Will Delay Interval (delay and cancel on reconnect); SUBSCRIBE with multiple topic filters and Subscription Options (No Local, Retain As Published, Retain Handling); shared subscriptions (`$share/{ShareName}/{filter}`); topic `$` rule (§4.7.2); PUBLISH with full properties, Topic Alias, Message Expiry; Session and flow control; Server DISCONNECT; Request/Response properties; wildcard topic matching; reason codes; all property types. CONNECT reserved-flag validation and SUBSCRIBE reserved bits / Retain Handling 3 validation ensure malformed packets are rejected per §4.13.
- **Normative checks:** CONNECT reserved flag (bit 0) must be 0 [MQTT-3.1.2-3]; SUBSCRIBE reserved bits (6–7) and Retain Handling value 3 rejected as malformed [MQTT-3.8.3-5]; No Local on shared subscription rejected with SUBACK failure for that filter [MQTT-3.8.3-4].

Compliance is aligned with §7.1.1 MQTT Server conformance clause.
