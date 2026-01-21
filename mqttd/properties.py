"""
MQTT 5.0 Properties Implementation

Properties are new in MQTT 5.0 and allow extensible metadata in packets.
Reference: https://docs.oasis-open.org/mqtt/mqtt/v5.0/mqtt-v5.0.html
"""

import struct
from typing import Dict, Any, List, Optional, Tuple
from enum import IntEnum


class PropertyType(IntEnum):
    """MQTT 5.0 Property Identifiers"""
    # Payload Format Indicator
    PAYLOAD_FORMAT_INDICATOR = 0x01
    # Message Expiry Interval
    MESSAGE_EXPIRY_INTERVAL = 0x02
    # Content Type
    CONTENT_TYPE = 0x03
    # Response Topic
    RESPONSE_TOPIC = 0x08
    # Correlation Data
    CORRELATION_DATA = 0x09
    # Subscription Identifier
    SUBSCRIPTION_IDENTIFIER = 0x0B
    # Session Expiry Interval
    SESSION_EXPIRY_INTERVAL = 0x11
    # Assigned Client Identifier
    ASSIGNED_CLIENT_IDENTIFIER = 0x12
    # Server Keep Alive
    SERVER_KEEP_ALIVE = 0x13
    # Authentication Method
    AUTHENTICATION_METHOD = 0x15
    # Authentication Data
    AUTHENTICATION_DATA = 0x16
    # Request Problem Information
    REQUEST_PROBLEM_INFORMATION = 0x17
    # Will Delay Interval
    WILL_DELAY_INTERVAL = 0x18
    # Request Response Information
    REQUEST_RESPONSE_INFORMATION = 0x19
    # Response Information
    RESPONSE_INFORMATION = 0x1A
    # Server Reference
    SERVER_REFERENCE = 0x1C
    # Reason String
    REASON_STRING = 0x1F
    # Receive Maximum
    RECEIVE_MAXIMUM = 0x21
    # Topic Alias Maximum
    TOPIC_ALIAS_MAXIMUM = 0x22
    # Topic Alias
    TOPIC_ALIAS = 0x23
    # Maximum QoS
    MAXIMUM_QOS = 0x24
    # Retain Available
    RETAIN_AVAILABLE = 0x25
    # User Property
    USER_PROPERTY = 0x26
    # Maximum Packet Size
    MAXIMUM_PACKET_SIZE = 0x27
    # Wildcard Subscription Available
    WILDCARD_SUBSCRIPTION_AVAILABLE = 0x28
    # Subscription Identifier Available
    SUBSCRIPTION_IDENTIFIER_AVAILABLE = 0x29
    # Shared Subscription Available
    SHARED_SUBSCRIPTION_AVAILABLE = 0x2A


class PropertyEncoder:
    """Encode/decode MQTT 5.0 Properties"""
    
    @staticmethod
    def encode_properties(properties: Dict[int, Any]) -> bytes:
        """
        Encode properties to bytes.
        
        Args:
            properties: Dictionary mapping PropertyType to value
            
        Returns:
            Encoded properties bytes
        """
        result = bytearray()
        
        for prop_id, value in sorted(properties.items()):
            # Handle subscription identifier list specially (can have multiple entries)
            if prop_id == PropertyType.SUBSCRIPTION_IDENTIFIER and isinstance(value, list):
                for sub_id in value:
                    result.append(prop_id)
                    result.extend(PropertyEncoder._encode_variable_byte_integer(sub_id))
                continue
            
            result.append(prop_id)
            
            if prop_id == PropertyType.PAYLOAD_FORMAT_INDICATOR:
                # Byte (0 or 1)
                result.append(int(value))
            
            elif prop_id == PropertyType.MESSAGE_EXPIRY_INTERVAL:
                # Four Byte Integer
                result.extend(struct.pack('>I', value))
            
            elif prop_id == PropertyType.CONTENT_TYPE:
                # UTF-8 Encoded String
                result.extend(PropertyEncoder._encode_string(value))
            
            elif prop_id == PropertyType.RESPONSE_TOPIC:
                # UTF-8 Encoded String
                result.extend(PropertyEncoder._encode_string(value))
            
            elif prop_id == PropertyType.CORRELATION_DATA:
                # Binary Data
                result.extend(struct.pack('>H', len(value)) + value)
            
            elif prop_id == PropertyType.SUBSCRIPTION_IDENTIFIER:
                # Variable Byte Integer (1-4 bytes) - can be a list (multiple subscription IDs)
                # Per MQTT 5.0 spec, multiple subscription identifiers are encoded as multiple
                # properties with the same property identifier
                if isinstance(value, list):
                    for sub_id in value:
                        result.append(PropertyType.SUBSCRIPTION_IDENTIFIER)
                        result.extend(PropertyEncoder._encode_variable_byte_integer(sub_id))
                else:
                    result.extend(PropertyEncoder._encode_variable_byte_integer(value))
            
            elif prop_id == PropertyType.SESSION_EXPIRY_INTERVAL:
                # Four Byte Integer
                result.extend(struct.pack('>I', value))
            
            elif prop_id == PropertyType.ASSIGNED_CLIENT_IDENTIFIER:
                # UTF-8 Encoded String
                result.extend(PropertyEncoder._encode_string(value))
            
            elif prop_id == PropertyType.SERVER_KEEP_ALIVE:
                # Two Byte Integer
                result.extend(struct.pack('>H', value))
            
            elif prop_id == PropertyType.AUTHENTICATION_METHOD:
                # UTF-8 Encoded String
                result.extend(PropertyEncoder._encode_string(value))
            
            elif prop_id == PropertyType.AUTHENTICATION_DATA:
                # Binary Data
                result.extend(struct.pack('>H', len(value)) + value)
            
            elif prop_id == PropertyType.REQUEST_PROBLEM_INFORMATION:
                # Byte (0 or 1)
                result.append(int(value))
            
            elif prop_id == PropertyType.WILL_DELAY_INTERVAL:
                # Four Byte Integer
                result.extend(struct.pack('>I', value))
            
            elif prop_id == PropertyType.REQUEST_RESPONSE_INFORMATION:
                # Byte (0 or 1)
                result.append(int(value))
            
            elif prop_id == PropertyType.RESPONSE_INFORMATION:
                # UTF-8 Encoded String
                result.extend(PropertyEncoder._encode_string(value))
            
            elif prop_id == PropertyType.SERVER_REFERENCE:
                # UTF-8 Encoded String
                result.extend(PropertyEncoder._encode_string(value))
            
            elif prop_id == PropertyType.REASON_STRING:
                # UTF-8 Encoded String
                result.extend(PropertyEncoder._encode_string(value))
            
            elif prop_id == PropertyType.RECEIVE_MAXIMUM:
                # Two Byte Integer
                result.extend(struct.pack('>H', value))
            
            elif prop_id == PropertyType.TOPIC_ALIAS_MAXIMUM:
                # Two Byte Integer
                result.extend(struct.pack('>H', value))
            
            elif prop_id == PropertyType.TOPIC_ALIAS:
                # Two Byte Integer
                result.extend(struct.pack('>H', value))
            
            elif prop_id == PropertyType.MAXIMUM_QOS:
                # Byte (0 or 1)
                result.append(int(value))
            
            elif prop_id == PropertyType.RETAIN_AVAILABLE:
                # Byte (0 or 1)
                result.append(int(value))
            
            elif prop_id == PropertyType.USER_PROPERTY:
                # UTF-8 String Pair (name, value)
                if isinstance(value, (list, tuple)) and len(value) == 2:
                    result.extend(PropertyEncoder._encode_string(value[0]))
                    result.extend(PropertyEncoder._encode_string(value[1]))
                else:
                    raise ValueError(f"USER_PROPERTY must be a (name, value) pair, got {value}")
            
            elif prop_id == PropertyType.MAXIMUM_PACKET_SIZE:
                # Four Byte Integer
                result.extend(struct.pack('>I', value))
            
            elif prop_id == PropertyType.WILDCARD_SUBSCRIPTION_AVAILABLE:
                # Byte (0 or 1)
                result.append(int(value))
            
            elif prop_id == PropertyType.SUBSCRIPTION_IDENTIFIER_AVAILABLE:
                # Byte (0 or 1)
                result.append(int(value))
            
            elif prop_id == PropertyType.SHARED_SUBSCRIPTION_AVAILABLE:
                # Byte (0 or 1)
                result.append(int(value))
            
            else:
                raise ValueError(f"Unknown property type: {prop_id}")
        
        return bytes(result)
    
    @staticmethod
    def decode_properties(data: bytes, offset: int = 0) -> Tuple[Dict[int, Any], int]:
        """
        Decode properties from bytes.
        
        Args:
            data: Buffer containing properties
            offset: Starting offset
            
        Returns:
            Tuple of (properties_dict, bytes_consumed)
        """
        properties = {}
        pos = offset
        
        while pos < len(data):
            prop_id = data[pos]
            pos += 1
            
            if prop_id == PropertyType.PAYLOAD_FORMAT_INDICATOR:
                properties[prop_id] = data[pos]
                pos += 1
            
            elif prop_id == PropertyType.MESSAGE_EXPIRY_INTERVAL:
                properties[prop_id] = struct.unpack('>I', data[pos:pos+4])[0]
                pos += 4
            
            elif prop_id == PropertyType.CONTENT_TYPE:
                value, pos = PropertyEncoder._decode_string(data, pos)
                properties[prop_id] = value
            
            elif prop_id == PropertyType.RESPONSE_TOPIC:
                value, pos = PropertyEncoder._decode_string(data, pos)
                properties[prop_id] = value
            
            elif prop_id == PropertyType.CORRELATION_DATA:
                length = struct.unpack('>H', data[pos:pos+2])[0]
                pos += 2
                properties[prop_id] = data[pos:pos+length]
                pos += length
            
            elif prop_id == PropertyType.SUBSCRIPTION_IDENTIFIER:
                value, consumed = PropertyEncoder._decode_variable_byte_integer(data, pos)
                # Subscription identifier can appear multiple times - accumulate in list
                if prop_id not in properties:
                    properties[prop_id] = []
                properties[prop_id].append(value)
                pos += consumed
            
            elif prop_id == PropertyType.SESSION_EXPIRY_INTERVAL:
                properties[prop_id] = struct.unpack('>I', data[pos:pos+4])[0]
                pos += 4
            
            elif prop_id == PropertyType.ASSIGNED_CLIENT_IDENTIFIER:
                value, pos = PropertyEncoder._decode_string(data, pos)
                properties[prop_id] = value
            
            elif prop_id == PropertyType.SERVER_KEEP_ALIVE:
                properties[prop_id] = struct.unpack('>H', data[pos:pos+2])[0]
                pos += 2
            
            elif prop_id == PropertyType.AUTHENTICATION_METHOD:
                value, pos = PropertyEncoder._decode_string(data, pos)
                properties[prop_id] = value
            
            elif prop_id == PropertyType.AUTHENTICATION_DATA:
                length = struct.unpack('>H', data[pos:pos+2])[0]
                pos += 2
                properties[prop_id] = data[pos:pos+length]
                pos += length
            
            elif prop_id == PropertyType.REQUEST_PROBLEM_INFORMATION:
                properties[prop_id] = data[pos]
                pos += 1
            
            elif prop_id == PropertyType.WILL_DELAY_INTERVAL:
                properties[prop_id] = struct.unpack('>I', data[pos:pos+4])[0]
                pos += 4
            
            elif prop_id == PropertyType.REQUEST_RESPONSE_INFORMATION:
                properties[prop_id] = data[pos]
                pos += 1
            
            elif prop_id == PropertyType.RESPONSE_INFORMATION:
                value, pos = PropertyEncoder._decode_string(data, pos)
                properties[prop_id] = value
            
            elif prop_id == PropertyType.SERVER_REFERENCE:
                value, pos = PropertyEncoder._decode_string(data, pos)
                properties[prop_id] = value
            
            elif prop_id == PropertyType.REASON_STRING:
                value, pos = PropertyEncoder._decode_string(data, pos)
                properties[prop_id] = value
            
            elif prop_id == PropertyType.RECEIVE_MAXIMUM:
                properties[prop_id] = struct.unpack('>H', data[pos:pos+2])[0]
                pos += 2
            
            elif prop_id == PropertyType.TOPIC_ALIAS_MAXIMUM:
                properties[prop_id] = struct.unpack('>H', data[pos:pos+2])[0]
                pos += 2
            
            elif prop_id == PropertyType.TOPIC_ALIAS:
                properties[prop_id] = struct.unpack('>H', data[pos:pos+2])[0]
                pos += 2
            
            elif prop_id == PropertyType.MAXIMUM_QOS:
                properties[prop_id] = data[pos]
                pos += 1
            
            elif prop_id == PropertyType.RETAIN_AVAILABLE:
                properties[prop_id] = data[pos]
                pos += 1
            
            elif prop_id == PropertyType.USER_PROPERTY:
                name, pos = PropertyEncoder._decode_string(data, pos)
                value, pos = PropertyEncoder._decode_string(data, pos)
                # User properties can appear multiple times
                if prop_id not in properties:
                    properties[prop_id] = []
                properties[prop_id].append((name, value))
            
            elif prop_id == PropertyType.MAXIMUM_PACKET_SIZE:
                properties[prop_id] = struct.unpack('>I', data[pos:pos+4])[0]
                pos += 4
            
            elif prop_id == PropertyType.WILDCARD_SUBSCRIPTION_AVAILABLE:
                properties[prop_id] = data[pos]
                pos += 1
            
            elif prop_id == PropertyType.SUBSCRIPTION_IDENTIFIER_AVAILABLE:
                properties[prop_id] = data[pos]
                pos += 1
            
            elif prop_id == PropertyType.SHARED_SUBSCRIPTION_AVAILABLE:
                properties[prop_id] = data[pos]
                pos += 1
            
            else:
                # Unknown property - skip it (per spec)
                break
        
        return properties, pos - offset
    
    @staticmethod
    def _encode_string(s: str) -> bytes:
        """Encode UTF-8 string with 2-byte length prefix"""
        utf8_bytes = s.encode('utf-8')
        return struct.pack('>H', len(utf8_bytes)) + utf8_bytes
    
    @staticmethod
    def _decode_string(data: bytes, offset: int) -> Tuple[str, int]:
        """Decode UTF-8 string with 2-byte length prefix"""
        if offset + 2 > len(data):
            raise ValueError("Insufficient data for string length")
        str_len = struct.unpack('>H', data[offset:offset+2])[0]
        offset += 2
        if offset + str_len > len(data):
            raise ValueError("Insufficient data for string content")
        string = data[offset:offset+str_len].decode('utf-8')
        return string, offset + str_len
    
    @staticmethod
    def _encode_variable_byte_integer(value: int) -> bytes:
        """Encode variable byte integer (1-4 bytes)"""
        result = bytearray()
        while True:
            encoded_byte = value % 128
            value //= 128
            if value > 0:
                encoded_byte |= 0x80
            result.append(encoded_byte)
            if value == 0:
                break
            if len(result) >= 4:
                raise ValueError("Variable byte integer too large")
        return bytes(result)
    
    @staticmethod
    def _decode_variable_byte_integer(data: bytes, offset: int) -> Tuple[int, int]:
        """Decode variable byte integer"""
        multiplier = 1
        value = 0
        bytes_consumed = 0
        
        for i in range(offset, min(offset + 4, len(data))):
            encoded = data[i]
            value += (encoded & 0x7F) * multiplier
            multiplier *= 128
            bytes_consumed += 1
            if not (encoded & 0x80):
                break
        
        return value, bytes_consumed
