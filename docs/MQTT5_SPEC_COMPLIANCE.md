# MQTT v5.0 Specification Compliance Checklist

Reference: [OASIS MQTT v5.0](https://docs.oasis-open.org/mqtt/mqtt/v5.0/mqtt-v5.0.html)

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
| **AUTH**  | **15** | ❌   | Enhanced authentication not implemented |

## CONNECT / CONNACK (§3.1, §3.2)

| Feature | Status | Notes |
|---------|--------|-------|
| Protocol name "MQTT", version 5 | ✅ | |
| Connect flags (Clean Start, Will, User/Password) | ✅ | |
| CONNECT properties (all 32 types) | ✅ | Encoded/decoded in `properties.py`, `protocol_v5.py` |
| Zero-length ClientID → Assigned Client Identifier | ❌ | Server does not assign or return in CONNACK |
| Will message + Will properties | ✅ | |
| Will Delay Interval (scheduling/cancel on reconnect) | ❌ | Property present; delay/cancel logic not implemented |
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
| **Multiple topic filters in one SUBSCRIBE** | ❌ | Only one topic parsed per packet |
| **Subscription Options byte** | ❌ | No Local, Retain As Published, Retain Handling not parsed or applied |
| SUBACK reason code per topic filter | ✅ | Builder supports list; single-topic only in practice |

## Shared Subscriptions (§4.8.2)

| Feature | Status | Notes |
|---------|--------|-------|
| `$share/{ShareName}/{filter}` | ❌ | Not supported; CONNACK sends `shared_subscription_available=0` |

## UNSUBSCRIBE / UNSUBACK (§3.10, §3.11)

| Feature | Status | Notes |
|---------|--------|-------|
| Multiple topic filters | ⚠️ | Verify parser returns list; UNSUBACK builder supports multiple reason codes |

## DISCONNECT (§3.14)

| Feature | Status | Notes |
|---------|--------|-------|
| Server sends DISCONNECT with reason code | ✅ | e.g. Session Taken Over (0x8E) |
| DISCONNECT properties | ✅ | Builder; server must not send Session Expiry Interval |

## AUTH – Enhanced Authentication (§3.15, §4.12)

| Feature | Status | Notes |
|---------|--------|-------|
| AUTH packet type and exchange | ❌ | No AUTH in `MQTTMessageType` or handling |
| CONNECT/CONNACK Auth Method/Data | ✅ | Properties only; no challenge/response flow |

## Request/Response (§4.10)

| Feature | Status | Notes |
|---------|--------|-------|
| Response Topic, Correlation Data on PUBLISH | ✅ | Forwarded |
| Request/Response Information in CONNECT/CONNACK | ✅ | In builders |

## Topic Names and Filters (§4.7)

| Feature | Status | Notes |
|---------|--------|-------|
| Wildcards `+`, `#` | ✅ | `decorators.topic_matches` |
| **Topics starting with `$`** | ❌ | Filters starting with `#`/`+` must not match `$` topic names; not enforced |

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

- **Implemented:** CONNECT/CONNACK (except zero-length ClientID), PUBLISH with full properties, Topic Alias, Message Expiry, Session and flow control, Server DISCONNECT, Request/Response properties, wildcard topic matching, reason codes, and all property types.
- **Not implemented or partial:** AUTH packet and enhanced auth, zero-length ClientID / Assigned Client Identifier, SUBSCRIBE multiple topic filters and Subscription Options (No Local, Retain As Published, Retain Handling), shared subscriptions, Will Delay Interval behavior, and topic `$` rule in matching.

To improve compliance: add AUTH handling, zero-length ClientID support, full SUBSCRIBE payload parsing (multiple filters + options), `$` rule in `topic_matches`, and Will Delay Interval scheduling.
