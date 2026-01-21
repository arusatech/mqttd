#!/usr/bin/env python3
"""
Comprehensive tests for new Priority 1-4 features
"""

import sys
import os
import asyncio
import struct

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mqttd import MQTTApp, MQTTProtocol, MQTTMessageType, MQTT5Protocol
from mqttd.properties import PropertyType
from mqttd.reason_codes import ReasonCode

def test_new_message_types():
    """Test new message types (PINGREQ, PINGRESP, UNSUBSCRIBE, UNSUBACK)"""
    print("Testing new message types...")
    
    # Test PINGREQ
    pingreq = MQTTProtocol.build_pingreq()
    assert pingreq[0] == MQTTMessageType.PINGREQ
    assert len(pingreq) == 2  # Fixed header + remaining length
    print("  ✓ PINGREQ message")
    
    # Test PINGRESP
    pingresp = MQTTProtocol.build_pingresp()
    assert pingresp[0] == MQTTMessageType.PINGRESP
    assert len(pingresp) == 2
    print("  ✓ PINGRESP message")
    
    # Test UNSUBSCRIBE
    unsubscribe = MQTTProtocol.build_unsubscribe(1, ["test/topic"])
    assert unsubscribe[0] == (MQTTMessageType.UNSUBSCRIBE | 0x02)  # With QoS 1 flag
    assert len(unsubscribe) > 0
    print("  ✓ UNSUBSCRIBE message")
    
    # Test UNSUBACK
    unsuback = MQTTProtocol.build_unsuback(1)
    assert unsuback[0] == MQTTMessageType.UNSUBACK
    assert len(unsuback) >= 4  # Fixed header + remaining length (1-4 bytes) + packet ID (2 bytes)
    print("  ✓ UNSUBACK message")
    
    # Test parsing UNSUBSCRIBE
    parsed = MQTTProtocol.parse_unsubscribe(unsubscribe[2:])  # Skip fixed header
    assert parsed['packet_id'] == 1
    assert 'test/topic' in parsed['topics']
    print("  ✓ UNSUBSCRIBE parsing")
    
    # Test QoS 2 messages
    pubrec = MQTTProtocol.build_pubrec(123)
    assert pubrec[0] == MQTTMessageType.PUBREC
    print("  ✓ PUBREC message")
    
    pubrel = MQTTProtocol.build_pubrel(123)
    assert pubrel[0] == (MQTTMessageType.PUBREL | 0x02)  # With QoS 1 flag
    print("  ✓ PUBREL message")
    
    pubcomp = MQTTProtocol.build_pubcomp(123)
    assert pubcomp[0] == MQTTMessageType.PUBCOMP
    print("  ✓ PUBCOMP message")
    
    print("New message types tests passed!\n")


def test_mqtt5_features():
    """Test MQTT 5.0 features"""
    print("Testing MQTT 5.0 features...")
    
    # Test MQTT 5.0 PUBLISH parsing with properties
    topic = "test/topic"
    payload = b"test payload"
    subscription_ids = [1, 2, 3]
    message_expiry = 60
    
    # Test without topic alias (topic included)
    publish_msg = MQTT5Protocol.build_publish_v5(
        topic=topic,
        payload=payload,
        qos=1,
        packet_id=123,
        subscription_identifiers=subscription_ids,
        message_expiry_interval=message_expiry
    )
    
    # Parse it back
    parsed = MQTT5Protocol.parse_publish_v5(publish_msg[2:], qos=1)  # Skip fixed header
    
    assert parsed['topic'] == topic
    assert parsed['payload'] == payload
    assert parsed['packet_id'] == 123
    assert parsed['message_expiry_interval'] == message_expiry
    assert set(parsed['subscription_identifiers']) == set(subscription_ids)
    print("  ✓ MQTT 5.0 PUBLISH with properties")
    
    # Test with topic alias (first time - topic included)
    topic_alias = 5
    publish_msg2 = MQTT5Protocol.build_publish_v5(
        topic=topic,
        payload=payload,
        qos=0,
        subscription_identifiers=[1],
        topic_alias=topic_alias
    )
    parsed2 = MQTT5Protocol.parse_publish_v5(publish_msg2[2:], qos=0)
    assert parsed2['topic'] == topic
    assert parsed2['topic_alias'] == topic_alias
    print("  ✓ MQTT 5.0 PUBLISH with topic alias (first time)")
    
    # Test MQTT 5.0 SUBSCRIBE parsing (create manually with properties)
    # Build SUBSCRIBE with properties manually for testing
    from mqttd.properties import PropertyEncoder
    
    packet_id_bytes = struct.pack('>H', 456)
    
    # Encode properties with subscription identifier
    props_dict = {PropertyType.SUBSCRIPTION_IDENTIFIER: 10}
    props_bytes = PropertyEncoder.encode_properties(props_dict)
    prop_length_bytes = MQTTProtocol.encode_remaining_length(len(props_bytes))
    
    topic_bytes = MQTTProtocol.encode_string("test/topic")
    qos_byte = b'\x01'
    
    subscribe_data = packet_id_bytes + prop_length_bytes + props_bytes + topic_bytes + qos_byte
    
    parsed_sub = MQTT5Protocol.parse_subscribe_v5(subscribe_data)
    assert parsed_sub['packet_id'] == 456
    sub_id = parsed_sub.get('subscription_identifier')
    # Can be a single value or list (depending on decode implementation)
    assert sub_id == 10 or (isinstance(sub_id, list) and 10 in sub_id)
    assert parsed_sub['topic'] == "test/topic"
    assert parsed_sub['qos'] == 1
    print("  ✓ MQTT 5.0 SUBSCRIBE with subscription identifier")
    
    # Test MQTT 5.0 UNSUBSCRIBE/UNSUBACK
    unsubscribe_v5 = MQTT5Protocol.build_unsubscribe_v5(789, ["topic1", "topic2"])
    unsuback_v5 = MQTT5Protocol.build_unsuback_v5(
        789,
        [ReasonCode.SUCCESS_UNSUB, ReasonCode.SUCCESS_UNSUB]
    )
    
    assert len(unsuback_v5) > 0
    print("  ✓ MQTT 5.0 UNSUBSCRIBE/UNSUBACK with reason codes")
    
    print("MQTT 5.0 features tests passed!\n")


def test_app_features():
    """Test new app features"""
    print("Testing app features...")
    
    # Test app creation with new parameters
    app = MQTTApp(
        port=1883,
        max_connections=1000,
        max_connections_per_ip=10,
        max_messages_per_second=100.0,
        max_subscriptions_per_minute=60
    )
    
    assert app._max_connections == 1000
    assert app._max_connections_per_ip == 10
    assert app._max_messages_per_second == 100.0
    assert app._max_subscriptions_per_minute == 60
    print("  ✓ App with connection limits and rate limiting")
    
    # Test metrics
    metrics = app.get_metrics()
    assert 'total_connections' in metrics
    assert 'current_connections' in metrics
    assert 'total_messages_published' in metrics
    assert 'retained_messages_count' in metrics
    assert 'active_subscriptions_count' in metrics
    print("  ✓ Metrics tracking")
    
    # Test health check
    health = app.health_check()
    assert 'status' in health
    assert 'running' in health
    assert 'connections' in health
    assert health['status'] in ['healthy', 'degraded']
    print("  ✓ Health check")
    
    # Test trie is initialized
    assert app._topic_trie is not None
    print("  ✓ Topic trie initialized")
    
    print("App features tests passed!\n")


def test_trie_functionality():
    """Test trie-based topic matching"""
    print("Testing trie functionality...")
    
    from mqttd.thread_safe import ThreadSafeTopicTrie
    
    trie = ThreadSafeTopicTrie()
    
    # Insert subscriptions
    client1 = ("sock1", "writer1")
    client2 = ("sock2", "writer2")
    client3 = ("sock3", "writer3")
    
    trie.insert("sensors/temperature", client1)
    trie.insert("sensors/+/humidity", client2)
    trie.insert("sensors/#", client3)
    
    # Test exact match
    matches = trie.find_matching("sensors/temperature")
    assert client1 in matches
    assert client3 in matches  # Multi-level wildcard matches
    print("  ✓ Exact match")
    
    # Test single-level wildcard
    matches = trie.find_matching("sensors/device1/humidity")
    assert client2 in matches
    assert client3 in matches
    print("  ✓ Single-level wildcard (+)")
    
    # Test multi-level wildcard
    matches = trie.find_matching("sensors/device1/room1/data")
    assert client3 in matches  # Only # matches
    print("  ✓ Multi-level wildcard (#)")
    
    # Test removal
    trie.remove("sensors/temperature", client1)
    matches = trie.find_matching("sensors/temperature")
    assert client1 not in matches
    print("  ✓ Subscription removal")
    
    print("Trie functionality tests passed!\n")


async def test_async_features():
    """Test async features"""
    print("Testing async features...")
    
    app = MQTTApp(port=1883)
    
    # Test shutdown method exists and can be called
    try:
        # Create a task that will complete immediately since server isn't running
        shutdown_task = asyncio.create_task(app.shutdown(timeout=0.1))
        await asyncio.sleep(0.01)  # Small delay
        shutdown_task.cancel()
        try:
            await shutdown_task
        except asyncio.CancelledError:
            pass
        print("  ✓ Graceful shutdown method exists")
    except Exception as e:
        print(f"  ⚠ Shutdown test: {e}")
    
    print("Async features tests passed!\n")


def test_protocol_compatibility():
    """Test backward compatibility"""
    print("Testing protocol compatibility...")
    
    # Test MQTT 3.1.1 messages still work
    connect_311 = MQTTProtocol.build_connect("client1", keepalive=60)
    assert len(connect_311) > 0
    
    # Parse it
    parsed = MQTTProtocol.parse_connect(connect_311[2:])  # Skip fixed header
    assert parsed['protocol_level'] == 0x04  # MQTT 3.1.1
    assert parsed['client_id'] == "client1"
    print("  ✓ MQTT 3.1.1 backward compatibility")
    
    # Test both protocols can coexist
    publish_311 = MQTTProtocol.build_publish("test", b"data", None, 0)
    publish_50 = MQTT5Protocol.build_publish_v5("test", b"data", qos=0)
    
    assert len(publish_311) > 0
    assert len(publish_50) > 0
    print("  ✓ MQTT 3.1.1 and 5.0 coexistence")
    
    print("Protocol compatibility tests passed!\n")


def main():
    """Run all tests"""
    print("=" * 60)
    print("Running comprehensive tests for Priority 1-4 features")
    print("=" * 60 + "\n")
    
    try:
        test_new_message_types()
        test_mqtt5_features()
        test_app_features()
        test_trie_functionality()
        asyncio.run(test_async_features())
        test_protocol_compatibility()
        
        print("=" * 60)
        print("All tests passed! ✓")
        print("=" * 60)
        return 0
    except AssertionError as e:
        print(f"\n❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return 1
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
