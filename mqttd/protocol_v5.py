"""
MQTT 5.0 Protocol Implementation

Enhanced protocol methods for MQTT 5.0 with full properties support.
Reference: https://docs.oasis-open.org/mqtt/mqtt/v5.0/mqtt-v5.0.html
"""

import struct
from typing import Tuple, Optional, Dict, Any, List
from .protocol import MQTTProtocol, MQTTMessageType, MQTTConnectFlags
from .properties import PropertyEncoder, PropertyType
from .reason_codes import ReasonCode


class MQTT5Protocol:
    """MQTT 5.0 Protocol handler with full feature support"""
    
    @staticmethod
    def build_connect_v5(
        client_id: str,
        username: Optional[str] = None,
        password: Optional[str] = None,
        keepalive: int = 60,
        clean_start: bool = True,
        session_expiry_interval: Optional[int] = None,
        will_properties: Optional[Dict[int, Any]] = None,
        will_topic: Optional[str] = None,
        will_payload: Optional[bytes] = None,
        will_retain: bool = False,
        will_qos: int = 0,
        properties: Optional[Dict[int, Any]] = None,
        receive_maximum: Optional[int] = None,
        maximum_packet_size: Optional[int] = None,
        topic_alias_maximum: Optional[int] = None,
        request_response_information: Optional[int] = None,
        request_problem_information: Optional[int] = None,
        authentication_method: Optional[str] = None,
        authentication_data: Optional[bytes] = None
    ) -> bytes:
        """
        Build a MQTT 5.0 CONNECT message.
        
        Args:
            client_id: Client identifier
            username: Username (optional)
            password: Password (optional)
            keepalive: Keep alive interval in seconds
            clean_start: Clean Start flag (replaces Clean Session)
            session_expiry_interval: Session expiry interval in seconds (None = session expires on disconnect)
            will_properties: Will message properties
            will_topic: Will topic
            will_payload: Will message payload
            will_retain: Will retain flag
            will_qos: Will QoS level
            properties: CONNECT properties
            receive_maximum: Maximum number of QoS > 0 messages to receive
            maximum_packet_size: Maximum packet size the client will accept
            topic_alias_maximum: Maximum topic aliases
            request_response_information: Request response information (0 or 1)
            request_problem_information: Request problem information (0 or 1)
            authentication_method: Authentication method
            authentication_data: Authentication data
        """
        # Fixed header
        msg_type = MQTTMessageType.CONNECT
        
        # Variable header - Protocol Name
        protocol_name = MQTTProtocol.encode_string("MQTT")
        protocol_level = bytes([MQTTProtocol.PROTOCOL_LEVEL_5_0])
        
        # Connect flags (MQTT 5.0)
        connect_flags = 0x00
        if username:
            connect_flags |= MQTTConnectFlags.USERNAME
        if password:
            connect_flags |= MQTTConnectFlags.PASSWORD
        if will_topic:
            connect_flags |= MQTTConnectFlags.WILL_FLAG
            if will_retain:
                connect_flags |= MQTTConnectFlags.WILL_RETAIN
            if will_qos == 1:
                connect_flags |= MQTTConnectFlags.WILL_QOS_1
            elif will_qos == 2:
                connect_flags |= MQTTConnectFlags.WILL_QOS_2
        if clean_start:
            connect_flags |= 0x02  # Clean Start (bit 1)
        
        keepalive_bytes = struct.pack('>H', keepalive)
        
        # Properties
        connect_properties = {}
        if session_expiry_interval is not None:
            connect_properties[PropertyType.SESSION_EXPIRY_INTERVAL] = session_expiry_interval
        if receive_maximum is not None:
            connect_properties[PropertyType.RECEIVE_MAXIMUM] = receive_maximum
        if maximum_packet_size is not None:
            connect_properties[PropertyType.MAXIMUM_PACKET_SIZE] = maximum_packet_size
        if topic_alias_maximum is not None:
            connect_properties[PropertyType.TOPIC_ALIAS_MAXIMUM] = topic_alias_maximum
        if request_response_information is not None:
            connect_properties[PropertyType.REQUEST_RESPONSE_INFORMATION] = request_response_information
        if request_problem_information is not None:
            connect_properties[PropertyType.REQUEST_PROBLEM_INFORMATION] = request_problem_information
        if authentication_method:
            connect_properties[PropertyType.AUTHENTICATION_METHOD] = authentication_method
        if authentication_data:
            connect_properties[PropertyType.AUTHENTICATION_DATA] = authentication_data
        if properties:
            connect_properties.update(properties)
        
        # Encode properties
        properties_bytes = PropertyEncoder.encode_properties(connect_properties)
        properties_length = MQTTProtocol.encode_remaining_length(len(properties_bytes))
        
        # Will Properties (if will present)
        will_properties_bytes = b''
        if will_topic and will_properties:
            will_properties_bytes = PropertyEncoder.encode_properties(will_properties)
        
        # Payload
        payload = MQTTProtocol.encode_string(client_id)
        
        # Will (if present)
        if will_topic:
            if will_properties:
                will_props_len = MQTTProtocol.encode_remaining_length(len(will_properties_bytes))
                payload += will_props_len + will_properties_bytes
            else:
                payload += b'\x00'  # Will Properties length = 0
            payload += MQTTProtocol.encode_string(will_topic)
            if will_payload:
                payload += struct.pack('>H', len(will_payload)) + will_payload
        
        if username:
            payload += MQTTProtocol.encode_string(username)
        if password:
            payload += MQTTProtocol.encode_string(password)
        
        # Variable header
        variable_header = (
            protocol_name +
            protocol_level +
            bytes([connect_flags]) +
            keepalive_bytes +
            properties_length +
            properties_bytes
        )
        
        # Remaining length
        remaining_length = len(variable_header) + len(payload)
        remaining_length_bytes = MQTTProtocol.encode_remaining_length(remaining_length)
        
        # Fixed header
        fixed_header = bytes([msg_type]) + remaining_length_bytes
        
        return fixed_header + variable_header + payload
    
    @staticmethod
    def build_connack_v5(
        reason_code: ReasonCode = ReasonCode.SUCCESS,
        session_present: bool = False,
        session_expiry_interval: Optional[int] = None,
        receive_maximum: Optional[int] = None,
        maximum_qos: Optional[int] = None,
        retain_available: Optional[int] = None,
        maximum_packet_size: Optional[int] = None,
        assigned_client_identifier: Optional[str] = None,
        topic_alias_maximum: Optional[int] = None,
        reason_string: Optional[str] = None,
        user_properties: Optional[List[Tuple[str, str]]] = None,
        wildcard_subscription_available: Optional[int] = None,
        subscription_identifier_available: Optional[int] = None,
        shared_subscription_available: Optional[int] = None,
        server_keep_alive: Optional[int] = None,
        response_information: Optional[str] = None,
        server_reference: Optional[str] = None,
        authentication_method: Optional[str] = None,
        authentication_data: Optional[bytes] = None
    ) -> bytes:
        """
        Build a MQTT 5.0 CONNACK message.
        
        Args:
            reason_code: Reason code (Success = 0x00)
            session_present: Session present flag
            session_expiry_interval: Session expiry interval
            receive_maximum: Maximum receive value
            maximum_qos: Maximum QoS supported
            retain_available: Retain available (0 or 1)
            maximum_packet_size: Maximum packet size
            assigned_client_identifier: Assigned client identifier
            topic_alias_maximum: Topic alias maximum
            reason_string: Reason string
            user_properties: User properties list
            wildcard_subscription_available: Wildcard subscription available
            subscription_identifier_available: Subscription identifier available
            shared_subscription_available: Shared subscription available
            server_keep_alive: Server keep alive
            response_information: Response information
            server_reference: Server reference
            authentication_method: Authentication method
            authentication_data: Authentication data
        """
        msg_type = MQTTMessageType.CONNACK
        
        # Connect Acknowledge Flags
        connack_flags = 0x00
        if session_present:
            connack_flags = 0x01
        
        # Properties
        properties = {}
        if session_expiry_interval is not None:
            properties[PropertyType.SESSION_EXPIRY_INTERVAL] = session_expiry_interval
        if receive_maximum is not None:
            properties[PropertyType.RECEIVE_MAXIMUM] = receive_maximum
        if maximum_qos is not None:
            properties[PropertyType.MAXIMUM_QOS] = maximum_qos
        if retain_available is not None:
            properties[PropertyType.RETAIN_AVAILABLE] = retain_available
        if maximum_packet_size is not None:
            properties[PropertyType.MAXIMUM_PACKET_SIZE] = maximum_packet_size
        if assigned_client_identifier:
            properties[PropertyType.ASSIGNED_CLIENT_IDENTIFIER] = assigned_client_identifier
        if topic_alias_maximum is not None:
            properties[PropertyType.TOPIC_ALIAS_MAXIMUM] = topic_alias_maximum
        if reason_string:
            properties[PropertyType.REASON_STRING] = reason_string
        if user_properties:
            for name, value in user_properties:
                if PropertyType.USER_PROPERTY not in properties:
                    properties[PropertyType.USER_PROPERTY] = []
                properties[PropertyType.USER_PROPERTY].append((name, value))
        if wildcard_subscription_available is not None:
            properties[PropertyType.WILDCARD_SUBSCRIPTION_AVAILABLE] = wildcard_subscription_available
        if subscription_identifier_available is not None:
            properties[PropertyType.SUBSCRIPTION_IDENTIFIER_AVAILABLE] = subscription_identifier_available
        if shared_subscription_available is not None:
            properties[PropertyType.SHARED_SUBSCRIPTION_AVAILABLE] = shared_subscription_available
        if server_keep_alive is not None:
            properties[PropertyType.SERVER_KEEP_ALIVE] = server_keep_alive
        if response_information:
            properties[PropertyType.RESPONSE_INFORMATION] = response_information
        if server_reference:
            properties[PropertyType.SERVER_REFERENCE] = server_reference
        if authentication_method:
            properties[PropertyType.AUTHENTICATION_METHOD] = authentication_method
        if authentication_data:
            properties[PropertyType.AUTHENTICATION_DATA] = authentication_data
        
        # Encode properties
        properties_bytes = PropertyEncoder.encode_properties(properties)
        properties_length = MQTTProtocol.encode_remaining_length(len(properties_bytes))
        
        # Variable header
        variable_header = (
            bytes([connack_flags]) +
            bytes([reason_code.value]) +
            properties_length +
            properties_bytes
        )
        
        # Remaining length
        remaining_length = len(variable_header)
        remaining_length_bytes = MQTTProtocol.encode_remaining_length(remaining_length)
        
        return bytes([msg_type]) + remaining_length_bytes + variable_header
    
    @staticmethod
    def build_suback_v5(
        packet_id: int,
        reason_codes: List[ReasonCode],
        reason_string: Optional[str] = None,
        user_properties: Optional[List[Tuple[str, str]]] = None
    ) -> bytes:
        """Build a MQTT 5.0 SUBACK message"""
        msg_type = MQTTMessageType.SUBACK
        
        # Variable header: Packet Identifier
        variable_header = struct.pack('>H', packet_id)
        
        # Properties
        properties = {}
        if reason_string:
            properties[PropertyType.REASON_STRING] = reason_string
        if user_properties:
            for name, value in user_properties:
                if PropertyType.USER_PROPERTY not in properties:
                    properties[PropertyType.USER_PROPERTY] = []
                properties[PropertyType.USER_PROPERTY].append((name, value))
        
        properties_bytes = PropertyEncoder.encode_properties(properties)
        properties_length = MQTTProtocol.encode_remaining_length(len(properties_bytes))
        
        # Payload: Reason Codes
        payload = bytes([rc.value for rc in reason_codes])
        
        # Variable header with properties
        full_variable_header = variable_header + properties_length + properties_bytes
        
        remaining_length = len(full_variable_header) + len(payload)
        remaining_length_bytes = MQTTProtocol.encode_remaining_length(remaining_length)
        
        return bytes([msg_type]) + remaining_length_bytes + full_variable_header + payload
    
    @staticmethod
    def parse_subscribe_v5(data: bytes) -> Dict[str, Any]:
        """
        Parse a MQTT 5.0 SUBSCRIBE message with properties support.
        
        Returns:
            Dictionary with:
            - packet_id: Packet identifier
            - topic: Topic filter
            - qos: Requested QoS level
            - subscription_identifier: Subscription identifier if present
            - properties: MQTT 5.0 properties dict
        """
        offset = 0
        
        # Packet ID
        if offset + 2 > len(data):
            raise ValueError("Insufficient data for packet ID")
        packet_id = struct.unpack('>H', data[offset:offset+2])[0]
        offset += 2
        
        # Properties length
        properties = {}
        subscription_identifier = None
        
        if offset < len(data):
            prop_length, prop_length_bytes = MQTTProtocol.decode_remaining_length(data, offset)
            offset += prop_length_bytes
            
            if prop_length > 0 and offset + prop_length <= len(data):
                properties, _ = PropertyEncoder.decode_properties(data[offset:offset+prop_length], 0)
                offset += prop_length
                subscription_identifier = properties.get(PropertyType.SUBSCRIPTION_IDENTIFIER)
                # If it's a list (multiple subscription IDs), take the first one for SUBSCRIBE
                # (SUBSCRIBE typically has one subscription identifier)
                if isinstance(subscription_identifier, list):
                    subscription_identifier = subscription_identifier[0] if subscription_identifier else None
        
        # Topic
        topic, offset = MQTTProtocol.decode_string(data, offset)
        
        # QoS
        if offset >= len(data):
            raise ValueError("Insufficient data for QoS")
        qos = data[offset] & 0x03
        
        return {
            'packet_id': packet_id,
            'topic': topic,
            'qos': qos,
            'subscription_identifier': subscription_identifier,
            'properties': properties
        }
    
    @staticmethod
    def build_publish_v5(
        topic: str,
        payload: bytes,
        packet_id: Optional[int] = None,
        qos: int = 0,
        retain: bool = False,
        properties: Optional[Dict[int, Any]] = None,
        payload_format_indicator: Optional[int] = None,
        message_expiry_interval: Optional[int] = None,
        content_type: Optional[str] = None,
        response_topic: Optional[str] = None,
        correlation_data: Optional[bytes] = None,
        subscription_identifiers: Optional[List[int]] = None,
        topic_alias: Optional[int] = None,
        user_properties: Optional[List[Tuple[str, str]]] = None
    ) -> bytes:
        """Build a MQTT 5.0 PUBLISH message"""
        msg_type = MQTTMessageType.PUBLISH
        if qos > 0:
            msg_type |= (qos << 1)
        if retain:
            msg_type |= 0x01
        
        # Properties
        publish_properties = {}
        if payload_format_indicator is not None:
            publish_properties[PropertyType.PAYLOAD_FORMAT_INDICATOR] = payload_format_indicator
        if message_expiry_interval is not None:
            publish_properties[PropertyType.MESSAGE_EXPIRY_INTERVAL] = message_expiry_interval
        if content_type:
            publish_properties[PropertyType.CONTENT_TYPE] = content_type
        if response_topic:
            publish_properties[PropertyType.RESPONSE_TOPIC] = response_topic
        if correlation_data:
            publish_properties[PropertyType.CORRELATION_DATA] = correlation_data
        if subscription_identifiers:
            for sub_id in subscription_identifiers:
                if PropertyType.SUBSCRIPTION_IDENTIFIER not in publish_properties:
                    publish_properties[PropertyType.SUBSCRIPTION_IDENTIFIER] = []
                publish_properties[PropertyType.SUBSCRIPTION_IDENTIFIER].append(sub_id)
        if topic_alias is not None:
            publish_properties[PropertyType.TOPIC_ALIAS] = topic_alias
        if user_properties:
            for name, value in user_properties:
                if PropertyType.USER_PROPERTY not in publish_properties:
                    publish_properties[PropertyType.USER_PROPERTY] = []
                publish_properties[PropertyType.USER_PROPERTY].append((name, value))
        if properties:
            publish_properties.update(properties)
        
        properties_bytes = PropertyEncoder.encode_properties(publish_properties)
        properties_length = MQTTProtocol.encode_remaining_length(len(properties_bytes))
        
        # Variable header: Topic (include if present - can be set with alias for first time)
        # If topic_alias is provided but topic is empty/None, then we're using an existing alias
        variable_header = MQTTProtocol.encode_string(topic) if topic else b''
        if qos > 0 and packet_id is not None:
            variable_header += struct.pack('>H', packet_id)
        variable_header += properties_length + properties_bytes
        
        # Payload
        full_payload = variable_header + payload
        
        remaining_length = len(full_payload)
        remaining_length_bytes = MQTTProtocol.encode_remaining_length(remaining_length)
        
        return bytes([msg_type]) + remaining_length_bytes + full_payload
    
    @staticmethod
    def parse_publish_v5(data: bytes, qos: int = 0) -> Dict[str, Any]:
        """
        Parse a MQTT 5.0 PUBLISH message with properties support.
        
        Returns:
            Dictionary with:
            - topic: Topic name (or resolved from alias)
            - payload: Message payload
            - packet_id: Packet ID (for QoS > 0)
            - properties: MQTT 5.0 properties dict
            - topic_alias: Topic alias if present
            - message_expiry_interval: Expiry interval if present
            - subscription_identifiers: List of subscription IDs if present
        """
        offset = 0
        
        # Topic (may be empty if using alias)
        topic = None
        if offset + 2 <= len(data):
            topic_len = struct.unpack('>H', data[offset:offset+2])[0]
            offset += 2
            if topic_len > 0 and offset + topic_len <= len(data):
                topic = data[offset:offset+topic_len].decode('utf-8')
                offset += topic_len
        
        # Packet ID (for QoS > 0)
        packet_id = None
        if qos > 0:
            if offset + 2 > len(data):
                raise ValueError("Insufficient data for packet ID")
            packet_id = struct.unpack('>H', data[offset:offset+2])[0]
            offset += 2
        
        # Properties length
        properties = {}
        topic_alias = None
        message_expiry_interval = None
        subscription_identifiers = []
        
        if offset < len(data):
            prop_length, prop_length_bytes = MQTTProtocol.decode_remaining_length(data, offset)
            offset += prop_length_bytes
            
            if prop_length > 0 and offset + prop_length <= len(data):
                properties, _ = PropertyEncoder.decode_properties(data[offset:offset+prop_length], 0)
                offset += prop_length
                
                # Extract specific properties
                topic_alias = properties.get(PropertyType.TOPIC_ALIAS)
                message_expiry_interval = properties.get(PropertyType.MESSAGE_EXPIRY_INTERVAL)
                
                # Extract subscription identifiers (can be multiple)
                sub_id_value = properties.get(PropertyType.SUBSCRIPTION_IDENTIFIER)
                if sub_id_value is not None:
                    if isinstance(sub_id_value, list):
                        subscription_identifiers = sub_id_value
                    else:
                        subscription_identifiers = [sub_id_value]
        
        # Payload
        payload = data[offset:]
        
        return {
            'topic': topic,
            'payload': payload,
            'packet_id': packet_id,
            'properties': properties,
            'topic_alias': topic_alias,
            'message_expiry_interval': message_expiry_interval,
            'subscription_identifiers': subscription_identifiers,
        }
    
    @staticmethod
    def build_disconnect_v5(
        reason_code: ReasonCode = ReasonCode.NORMAL_DISCONNECTION,
        session_expiry_interval: Optional[int] = None,
        reason_string: Optional[str] = None,
        user_properties: Optional[List[Tuple[str, str]]] = None,
        server_reference: Optional[str] = None
    ) -> bytes:
        """Build a MQTT 5.0 DISCONNECT message"""
        msg_type = MQTTMessageType.DISCONNECT
        
        # Properties
        properties = {}
        if session_expiry_interval is not None:
            properties[PropertyType.SESSION_EXPIRY_INTERVAL] = session_expiry_interval
        if reason_string:
            properties[PropertyType.REASON_STRING] = reason_string
        if user_properties:
            for name, value in user_properties:
                if PropertyType.USER_PROPERTY not in properties:
                    properties[PropertyType.USER_PROPERTY] = []
                properties[PropertyType.USER_PROPERTY].append((name, value))
        if server_reference:
            properties[PropertyType.SERVER_REFERENCE] = server_reference
        
        properties_bytes = PropertyEncoder.encode_properties(properties)
        properties_length = MQTTProtocol.encode_remaining_length(len(properties_bytes))
        
        # Variable header: Reason Code + Properties
        variable_header = bytes([reason_code.value]) + properties_length + properties_bytes
        
        remaining_length = len(variable_header)
        remaining_length_bytes = MQTTProtocol.encode_remaining_length(remaining_length)
        
        return bytes([msg_type]) + remaining_length_bytes + variable_header
    
    @staticmethod
    def build_unsubscribe_v5(
        packet_id: int,
        topics: List[str],
        properties: Optional[Dict[int, Any]] = None,
        user_properties: Optional[List[Tuple[str, str]]] = None
    ) -> bytes:
        """Build a MQTT 5.0 UNSUBSCRIBE message"""
        msg_type = MQTTMessageType.UNSUBSCRIBE | 0x02  # QoS 1 required
        
        # Variable header: Packet Identifier
        variable_header = struct.pack('>H', packet_id)
        
        # Properties
        unsubscribe_properties = {}
        if user_properties:
            for name, value in user_properties:
                if PropertyType.USER_PROPERTY not in unsubscribe_properties:
                    unsubscribe_properties[PropertyType.USER_PROPERTY] = []
                unsubscribe_properties[PropertyType.USER_PROPERTY].append((name, value))
        if properties:
            unsubscribe_properties.update(properties)
        
        properties_bytes = PropertyEncoder.encode_properties(unsubscribe_properties)
        properties_length = MQTTProtocol.encode_remaining_length(len(properties_bytes))
        
        # Variable header with properties
        full_variable_header = variable_header + properties_length + properties_bytes
        
        # Payload: Topics (list of strings)
        payload = b''
        for topic in topics:
            payload += MQTTProtocol.encode_string(topic)
        
        remaining_length = len(full_variable_header) + len(payload)
        remaining_length_bytes = MQTTProtocol.encode_remaining_length(remaining_length)
        
        return bytes([msg_type]) + remaining_length_bytes + full_variable_header + payload
    
    @staticmethod
    def build_unsuback_v5(
        packet_id: int,
        reason_codes: List[ReasonCode],
        reason_string: Optional[str] = None,
        user_properties: Optional[List[Tuple[str, str]]] = None
    ) -> bytes:
        """Build a MQTT 5.0 UNSUBACK message"""
        msg_type = MQTTMessageType.UNSUBACK
        
        # Variable header: Packet Identifier
        variable_header = struct.pack('>H', packet_id)
        
        # Properties
        properties = {}
        if reason_string:
            properties[PropertyType.REASON_STRING] = reason_string
        if user_properties:
            for name, value in user_properties:
                if PropertyType.USER_PROPERTY not in properties:
                    properties[PropertyType.USER_PROPERTY] = []
                properties[PropertyType.USER_PROPERTY].append((name, value))
        
        properties_bytes = PropertyEncoder.encode_properties(properties)
        properties_length = MQTTProtocol.encode_remaining_length(len(properties_bytes))
        
        # Variable header with properties
        full_variable_header = variable_header + properties_length + properties_bytes
        
        # Payload: Reason Codes (one per topic)
        payload = bytes([rc.value for rc in reason_codes])
        
        remaining_length = len(full_variable_header) + len(payload)
        remaining_length_bytes = MQTTProtocol.encode_remaining_length(remaining_length)
        
        return bytes([msg_type]) + remaining_length_bytes + full_variable_header + payload
