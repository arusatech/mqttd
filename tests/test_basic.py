#!/usr/bin/env python3
"""
Basic test script for MQTTD package
"""

import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mqttd import MQTTApp, MQTTProtocol, MQTTMessageType

def test_protocol_encoding():
    """Test MQTT protocol encoding/decoding"""
    print("Testing protocol encoding...")
    
    # Test remaining length encoding
    test_lengths = [0, 1, 127, 128, 16383, 16384, 2097151, 2097152]
    for length in test_lengths:
        encoded = MQTTProtocol.encode_remaining_length(length)
        decoded, _ = MQTTProtocol.decode_remaining_length(encoded, 0)
        assert decoded == length, f"Length mismatch: {length} != {decoded}"
        print(f"  ✓ Length {length}: {encoded.hex()}")
    
    # Test string encoding
    test_string = "test_topic"
    encoded = MQTTProtocol.encode_string(test_string)
    decoded, _ = MQTTProtocol.decode_string(encoded, 0)
    assert decoded == test_string, f"String mismatch: {test_string} != {decoded}"
    print(f"  ✓ String encoding: {test_string}")
    
    # Test CONNECT message
    connect = MQTTProtocol.build_connect("test_client", "user", "pass")
    assert len(connect) > 0
    print(f"  ✓ CONNECT message: {len(connect)} bytes")
    
    # Test CONNACK
    connack = MQTTProtocol.build_connack(0)
    assert len(connack) == 4  # 1 byte type + 1 byte length + 2 bytes data
    print(f"  ✓ CONNACK message: {len(connack)} bytes")
    
    # Test SUBSCRIBE
    subscribe = MQTTProtocol.build_subscribe(1, "test/topic", 0)
    assert len(subscribe) > 0
    print(f"  ✓ SUBSCRIBE message: {len(subscribe)} bytes")
    
    # Test PUBLISH
    publish = MQTTProtocol.build_publish("test/topic", b"payload", None, 0)
    assert len(publish) > 0
    print(f"  ✓ PUBLISH message: {len(publish)} bytes")
    
    print("Protocol encoding tests passed!\n")

def test_app_creation():
    """Test MQTTApp creation"""
    print("Testing MQTTApp creation...")
    
    app = MQTTApp(port=1883)
    assert app.port == 1883
    assert app.host == "0.0.0.0"
    print("  ✓ Basic app creation")
    
    app = MQTTApp(host="127.0.0.1", port=8883)
    assert app.port == 8883
    assert app.host == "127.0.0.1"
    print("  ✓ App with custom host/port")
    
    print("MQTTApp creation tests passed!\n")

if __name__ == "__main__":
    print("Running MQTTD tests...\n")
    
    try:
        test_protocol_encoding()
        test_app_creation()
        
        print("All tests passed! ✓")
    except AssertionError as e:
        print(f"Test failed: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
