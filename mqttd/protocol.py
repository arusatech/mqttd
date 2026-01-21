"""
MQTT Protocol Implementation

Low-level MQTT protocol message encoding/decoding, compatible with MQTT 3.1.1
and MQTT 5.0. Supports both protocol versions with automatic detection.
Reference: https://docs.oasis-open.org/mqtt/mqtt/v5.0/mqtt-v5.0.html
"""

import struct
from typing import Tuple, Optional, Dict, Any, List
from enum import IntEnum

try:
    from .properties import PropertyEncoder, PropertyType
    from .reason_codes import ReasonCode
except ImportError:
    # Fallback if properties module not available
    PropertyEncoder = None
    PropertyType = None
    ReasonCode = None


class MQTTMessageType(IntEnum):
    """MQTT message types"""
    CONNECT = 0x10
    CONNACK = 0x20
    PUBLISH = 0x30
    PUBACK = 0x40
    PUBREC = 0x50
    PUBREL = 0x62
    PUBCOMP = 0x70
    SUBSCRIBE = 0x82
    SUBACK = 0x90
    UNSUBSCRIBE = 0xA2
    UNSUBACK = 0xB0
    PINGREQ = 0xC0
    PINGRESP = 0xD0
    DISCONNECT = 0xE0


class MQTTConnectFlags:
    """MQTT CONNECT flags"""
    USERNAME = 0x80
    PASSWORD = 0x40
    WILL_RETAIN = 0x20
    WILL_QOS_1 = 0x08
    WILL_QOS_2 = 0x18
    WILL_FLAG = 0x04
    CLEAN_SESSION = 0x02  # MQTT 3.1.1
    CLEAN_START = 0x02    # MQTT 5.0 (same bit)
    RESERVED = 0x01


class MQTTConnAckCode:
    """CONNACK return codes (MQTT 3.1.1)"""
    ACCEPTED = 0x00
    UNACCEPTABLE_PROTOCOL = 0x01
    IDENTIFIER_REJECTED = 0x02
    SERVER_UNAVAILABLE = 0x03
    BAD_USERNAME_PASSWORD = 0x04
    NOT_AUTHORIZED = 0x05


class MQTTProtocol:
    """MQTT Protocol handler for encoding/decoding messages"""
    
    PROTOCOL_NAME = b"MQTT"
    PROTOCOL_LEVEL_3_1_1 = 0x04  # MQTT 3.1.1
    PROTOCOL_LEVEL_5_0 = 0x05    # MQTT 5.0
    PROTOCOL_LEVEL = PROTOCOL_LEVEL_5_0  # Default to 5.0, support 3.1.1 for compatibility
    
    @staticmethod
    def encode_remaining_length(length: int) -> bytes:
        """
        Encode remaining length using MQTT variable-length encoding.
        
        Args:
            length: The length to encode (0 to 268,435,455)
            
        Returns:
            Encoded bytes (1-4 bytes)
        """
        if length < 0 or length > 268435455:
            raise ValueError(f"Invalid remaining length: {length}")
        
        encoded = bytearray()
        while True:
            encoded_byte = length % 128
            length //= 128
            if length > 0:
                encoded_byte |= 0x80
            encoded.append(encoded_byte)
            if length == 0:
                break
            if len(encoded) >= 4:
                raise ValueError("Remaining length too large")
        
        return bytes(encoded)
    
    @staticmethod
    def decode_remaining_length(data: bytes, offset: int = 0) -> Tuple[int, int]:
        """
        Decode remaining length from MQTT variable-length encoding.
        
        Args:
            data: Buffer containing encoded length
            offset: Starting offset in buffer
            
        Returns:
            Tuple of (decoded_length, bytes_consumed)
        """
        length = 0
        multiplier = 1
        bytes_consumed = 0
        
        for i in range(offset, min(offset + 4, len(data))):
            encoded = data[i]
            length += (encoded & 0x7F) * multiplier
            multiplier *= 128
            bytes_consumed += 1
            
            if not (encoded & 0x80):
                break
        
        return length, bytes_consumed
    
    @staticmethod
    def encode_string(s: str) -> bytes:
        """Encode a string as MQTT format: 2-byte length + UTF-8 bytes"""
        utf8_bytes = s.encode('utf-8')
        return struct.pack('>H', len(utf8_bytes)) + utf8_bytes
    
    @staticmethod
    def decode_string(data: bytes, offset: int = 0) -> Tuple[str, int]:
        """Decode a string from MQTT format"""
        if offset + 2 > len(data):
            raise ValueError("Insufficient data for string length")
        
        str_len = struct.unpack('>H', data[offset:offset+2])[0]
        offset += 2
        
        if offset + str_len > len(data):
            raise ValueError("Insufficient data for string content")
        
        string = data[offset:offset+str_len].decode('utf-8')
        return string, offset + str_len
    
    @staticmethod
    def build_connect(client_id: str, username: Optional[str] = None,
                     password: Optional[str] = None, keepalive: int = 60,
                     clean_session: bool = True) -> bytes:
        """Build a CONNECT message (MQTT 3.1.1)"""
        # Fixed header
        msg_type = MQTTMessageType.CONNECT
        
        # Variable header
        protocol_name = MQTTProtocol.encode_string("MQTT")
        protocol_level = bytes([MQTTProtocol.PROTOCOL_LEVEL_3_1_1])
        
        # Connect flags
        connect_flags = 0x00
        if clean_session:
            connect_flags |= MQTTConnectFlags.CLEAN_SESSION
        if username:
            connect_flags |= MQTTConnectFlags.USERNAME
        if password:
            connect_flags |= MQTTConnectFlags.PASSWORD
        
        keepalive_bytes = struct.pack('>H', keepalive)
        
        # Payload
        payload = MQTTProtocol.encode_string(client_id)
        if username:
            payload += MQTTProtocol.encode_string(username)
        if password:
            payload += MQTTProtocol.encode_string(password)
        
        # Variable header
        variable_header = protocol_name + protocol_level + bytes([connect_flags]) + keepalive_bytes
        
        # Remaining length
        remaining_length = len(variable_header) + len(payload)
        remaining_length_bytes = MQTTProtocol.encode_remaining_length(remaining_length)
        
        # Fixed header
        fixed_header = bytes([msg_type]) + remaining_length_bytes
        
        return fixed_header + variable_header + payload
    
    @staticmethod
    def build_connack(return_code: int = MQTTConnAckCode.ACCEPTED) -> bytes:
        """Build a CONNACK message (MQTT 3.1.1)"""
        msg_type = MQTTMessageType.CONNACK
        remaining_length = 2
        remaining_length_bytes = MQTTProtocol.encode_remaining_length(remaining_length)
        
        # Variable header: Connect Acknowledge Flags (1 byte) + Return Code (1 byte)
        variable_header = bytes([0x00, return_code])
        
        return bytes([msg_type]) + remaining_length_bytes + variable_header
    
    @staticmethod
    def build_subscribe(packet_id: int, topic: str, qos: int = 0) -> bytes:
        """Build a SUBSCRIBE message"""
        msg_type = MQTTMessageType.SUBSCRIBE | 0x02  # QoS 1 required for SUBSCRIBE
        
        # Variable header: Packet Identifier
        variable_header = struct.pack('>H', packet_id)
        
        # Payload: Topic + QoS
        payload = MQTTProtocol.encode_string(topic) + bytes([qos])
        
        remaining_length = len(variable_header) + len(payload)
        remaining_length_bytes = MQTTProtocol.encode_remaining_length(remaining_length)
        
        return bytes([msg_type]) + remaining_length_bytes + variable_header + payload
    
    @staticmethod
    def build_suback(packet_id: int, return_code: int = 0) -> bytes:
        """Build a SUBACK message"""
        msg_type = MQTTMessageType.SUBACK
        remaining_length = 3
        remaining_length_bytes = MQTTProtocol.encode_remaining_length(remaining_length)
        
        # Variable header: Packet Identifier + Return Code
        variable_header = struct.pack('>H', packet_id) + bytes([return_code])
        
        return bytes([msg_type]) + remaining_length_bytes + variable_header
    
    @staticmethod
    def build_publish(topic: str, payload: bytes, packet_id: Optional[int] = None,
                     qos: int = 0, retain: bool = False) -> bytes:
        """Build a PUBLISH message"""
        msg_type = MQTTMessageType.PUBLISH
        if qos > 0:
            msg_type |= (qos << 1)
        if retain:
            msg_type |= 0x01
        
        # Variable header: Topic
        variable_header = MQTTProtocol.encode_string(topic)
        
        # Packet ID for QoS > 0
        if qos > 0 and packet_id is not None:
            variable_header += struct.pack('>H', packet_id)
        
        # Payload
        full_payload = variable_header + payload
        
        remaining_length = len(full_payload)
        remaining_length_bytes = MQTTProtocol.encode_remaining_length(remaining_length)
        
        return bytes([msg_type]) + remaining_length_bytes + full_payload
    
    @staticmethod
    def build_disconnect() -> bytes:
        """Build a DISCONNECT message"""
        msg_type = MQTTMessageType.DISCONNECT
        remaining_length = 0
        remaining_length_bytes = MQTTProtocol.encode_remaining_length(remaining_length)
        
        return bytes([msg_type]) + remaining_length_bytes
    
    @staticmethod
    def build_pingreq() -> bytes:
        """Build a PINGREQ message"""
        msg_type = MQTTMessageType.PINGREQ
        remaining_length = 0
        remaining_length_bytes = MQTTProtocol.encode_remaining_length(remaining_length)
        
        return bytes([msg_type]) + remaining_length_bytes
    
    @staticmethod
    def build_pingresp() -> bytes:
        """Build a PINGRESP message"""
        msg_type = MQTTMessageType.PINGRESP
        remaining_length = 0
        remaining_length_bytes = MQTTProtocol.encode_remaining_length(remaining_length)
        
        return bytes([msg_type]) + remaining_length_bytes
    
    @staticmethod
    def build_unsubscribe(packet_id: int, topics: List[str]) -> bytes:
        """Build an UNSUBSCRIBE message"""
        msg_type = MQTTMessageType.UNSUBSCRIBE | 0x02  # QoS 1 required for UNSUBSCRIBE
        
        # Variable header: Packet Identifier
        variable_header = struct.pack('>H', packet_id)
        
        # Payload: Topics (list of strings)
        payload = b''
        for topic in topics:
            payload += MQTTProtocol.encode_string(topic)
        
        remaining_length = len(variable_header) + len(payload)
        remaining_length_bytes = MQTTProtocol.encode_remaining_length(remaining_length)
        
        return bytes([msg_type]) + remaining_length_bytes + variable_header + payload
    
    @staticmethod
    def build_unsuback(packet_id: int) -> bytes:
        """Build an UNSUBACK message (MQTT 3.1.1)"""
        msg_type = MQTTMessageType.UNSUBACK
        remaining_length = 2
        remaining_length_bytes = MQTTProtocol.encode_remaining_length(remaining_length)
        
        # Variable header: Packet Identifier
        variable_header = struct.pack('>H', packet_id)
        
        return bytes([msg_type]) + remaining_length_bytes + variable_header
    
    @staticmethod
    def parse_unsubscribe(data: bytes) -> Dict[str, Any]:
        """Parse an UNSUBSCRIBE message"""
        offset = 0
        
        # Packet ID
        if offset + 2 > len(data):
            raise ValueError("Insufficient data for packet ID")
        packet_id = struct.unpack('>H', data[offset:offset+2])[0]
        offset += 2
        
        # Topics (list of strings)
        topics = []
        while offset < len(data):
            topic, offset = MQTTProtocol.decode_string(data, offset)
            topics.append(topic)
        
        return {
            'packet_id': packet_id,
            'topics': topics
        }
    
    @staticmethod
    def build_puback(packet_id: int) -> bytes:
        """Build a PUBACK message"""
        msg_type = MQTTMessageType.PUBACK
        remaining_length = 2
        remaining_length_bytes = MQTTProtocol.encode_remaining_length(remaining_length)
        
        # Variable header: Packet Identifier
        variable_header = struct.pack('>H', packet_id)
        
        return bytes([msg_type]) + remaining_length_bytes + variable_header
    
    @staticmethod
    def build_pubrec(packet_id: int) -> bytes:
        """Build a PUBREC message (QoS 2 flow)"""
        msg_type = MQTTMessageType.PUBREC
        remaining_length = 2
        remaining_length_bytes = MQTTProtocol.encode_remaining_length(remaining_length)
        
        # Variable header: Packet Identifier
        variable_header = struct.pack('>H', packet_id)
        
        return bytes([msg_type]) + remaining_length_bytes + variable_header
    
    @staticmethod
    def build_pubrel(packet_id: int) -> bytes:
        """Build a PUBREL message (QoS 2 flow)"""
        msg_type = MQTTMessageType.PUBREL | 0x02  # QoS 1 required for PUBREL
        remaining_length = 2
        remaining_length_bytes = MQTTProtocol.encode_remaining_length(remaining_length)
        
        # Variable header: Packet Identifier
        variable_header = struct.pack('>H', packet_id)
        
        return bytes([msg_type]) + remaining_length_bytes + variable_header
    
    @staticmethod
    def build_pubcomp(packet_id: int) -> bytes:
        """Build a PUBCOMP message (QoS 2 flow)"""
        msg_type = MQTTMessageType.PUBCOMP
        remaining_length = 2
        remaining_length_bytes = MQTTProtocol.encode_remaining_length(remaining_length)
        
        # Variable header: Packet Identifier
        variable_header = struct.pack('>H', packet_id)
        
        return bytes([msg_type]) + remaining_length_bytes + variable_header
    
    @staticmethod
    def parse_fixed_header(data: bytes) -> Tuple[int, int, int]:
        """
        Parse MQTT fixed header.
        
        Returns:
            Tuple of (message_type, remaining_length, bytes_consumed)
        """
        if len(data) < 2:
            raise ValueError("Insufficient data for fixed header")
        
        message_type = data[0]
        remaining_length, length_bytes = MQTTProtocol.decode_remaining_length(data, 1)
        
        return message_type, remaining_length, 1 + length_bytes
    
    @staticmethod
    def parse_connect(data: bytes) -> Dict[str, Any]:
        """
        Parse a CONNECT message (MQTT 3.1.1 and basic MQTT 5.0).
        
        For MQTT 5.0, properties are parsed separately.
        """
        offset = 0
        
        # Protocol name
        protocol_name, offset = MQTTProtocol.decode_string(data, offset)
        
        # Protocol level
        if offset >= len(data):
            raise ValueError("Insufficient data for protocol level")
        protocol_level = data[offset]
        offset += 1
        
        # Connect flags
        if offset >= len(data):
            raise ValueError("Insufficient data for connect flags")
        connect_flags = data[offset]
        offset += 1
        
        # Keepalive
        if offset + 2 > len(data):
            raise ValueError("Insufficient data for keepalive")
        keepalive = struct.unpack('>H', data[offset:offset+2])[0]
        offset += 2
        
        # For MQTT 5.0, properties come next
        properties = {}
        if protocol_level == MQTTProtocol.PROTOCOL_LEVEL_5_0 and PropertyEncoder:
            # Properties length (variable byte integer)
            if offset < len(data):
                prop_length, prop_length_bytes = MQTTProtocol.decode_remaining_length(data, offset)
                offset += prop_length_bytes
                
                # Parse properties
                if prop_length > 0 and offset + prop_length <= len(data):
                    properties, _ = PropertyEncoder.decode_properties(data[offset:offset+prop_length], 0)
                    offset += prop_length
        
        # Client ID
        if offset >= len(data):
            raise ValueError("Insufficient data for client ID")
        client_id, offset = MQTTProtocol.decode_string(data, offset)
        
        # Will Properties (MQTT 5.0 only, if Will flag is set)
        will_properties = {}
        if protocol_level == MQTTProtocol.PROTOCOL_LEVEL_5_0 and (connect_flags & MQTTConnectFlags.WILL_FLAG):
            if offset < len(data):
                will_prop_length, will_prop_length_bytes = MQTTProtocol.decode_remaining_length(data, offset)
                offset += will_prop_length_bytes
                if will_prop_length > 0 and offset + will_prop_length <= len(data) and PropertyEncoder:
                    will_properties, _ = PropertyEncoder.decode_properties(data[offset:offset+will_prop_length], 0)
                    offset += will_prop_length
        
        # Will Topic and Payload (if Will flag is set)
        will_topic = None
        will_payload = None
        if connect_flags & MQTTConnectFlags.WILL_FLAG:
            will_topic, offset = MQTTProtocol.decode_string(data, offset)
            if offset + 2 <= len(data):
                will_payload_len = struct.unpack('>H', data[offset:offset+2])[0]
                offset += 2
                if offset + will_payload_len <= len(data):
                    will_payload = data[offset:offset+will_payload_len]
                    offset += will_payload_len
        
        # Username (if flag set)
        username = None
        if connect_flags & MQTTConnectFlags.USERNAME:
            username, offset = MQTTProtocol.decode_string(data, offset)
        
        # Password (if flag set)
        password = None
        if connect_flags & MQTTConnectFlags.PASSWORD:
            password, offset = MQTTProtocol.decode_string(data, offset)
        
        # Extract Clean Start (MQTT 5.0) or Clean Session (MQTT 3.1.1)
        clean_start = bool(connect_flags & MQTTConnectFlags.CLEAN_START)
        clean_session = clean_start  # For compatibility
        
        # Extract Session Expiry Interval from properties (MQTT 5.0)
        session_expiry_interval = 0
        if protocol_level == MQTTProtocol.PROTOCOL_LEVEL_5_0 and PropertyType:
            session_expiry_interval = properties.get(PropertyType.SESSION_EXPIRY_INTERVAL, 0)
            # If not specified and Clean Start = 0, default is max (0xFFFFFFFF)
            if not clean_start and PropertyType.SESSION_EXPIRY_INTERVAL not in properties:
                session_expiry_interval = 0xFFFFFFFF
        
        return {
            'protocol_name': protocol_name,
            'protocol_level': protocol_level,
            'connect_flags': connect_flags,
            'keepalive': keepalive,
            'properties': properties,
            'client_id': client_id,
            'username': username,
            'password': password,
            'clean_session': clean_session,
            'clean_start': clean_start,
            'session_expiry_interval': session_expiry_interval,
            'will_topic': will_topic,
            'will_payload': will_payload,
            'will_properties': will_properties,
        }
    
    @staticmethod
    def parse_subscribe(data: bytes) -> Dict[str, Any]:
        """Parse a SUBSCRIBE message"""
        offset = 0
        
        # Packet ID
        if offset + 2 > len(data):
            raise ValueError("Insufficient data for packet ID")
        packet_id = struct.unpack('>H', data[offset:offset+2])[0]
        offset += 2
        
        # Topic
        topic, offset = MQTTProtocol.decode_string(data, offset)
        
        # QoS
        if offset >= len(data):
            raise ValueError("Insufficient data for QoS")
        qos = data[offset] & 0x03
        
        return {
            'packet_id': packet_id,
            'topic': topic,
            'qos': qos
        }
    
    @staticmethod
    def parse_publish(data: bytes, qos: int = 0) -> Dict[str, Any]:
        """Parse a PUBLISH message"""
        offset = 0
        
        # Topic
        topic, offset = MQTTProtocol.decode_string(data, offset)
        
        # Packet ID (for QoS > 0)
        packet_id = None
        if qos > 0:
            if offset + 2 > len(data):
                raise ValueError("Insufficient data for packet ID")
            packet_id = struct.unpack('>H', data[offset:offset+2])[0]
            offset += 2
        
        # Payload
        payload = data[offset:]
        
        return {
            'topic': topic,
            'payload': payload,
            'packet_id': packet_id
        }
