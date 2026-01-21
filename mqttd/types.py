"""
Type definitions for MQTTD
"""

from dataclasses import dataclass
from typing import Optional, Any
from enum import IntEnum


class QoS(IntEnum):
    """Quality of Service levels"""
    AT_MOST_ONCE = 0
    AT_LEAST_ONCE = 1
    EXACTLY_ONCE = 2


@dataclass
class MQTTMessage:
    """Represents an MQTT message"""
    topic: str
    payload: bytes
    qos: int = 0
    retain: bool = False
    packet_id: Optional[int] = None
    
    @property
    def payload_str(self) -> str:
        """Get payload as string"""
        return self.payload.decode('utf-8', errors='replace')
    
    @property
    def payload_json(self) -> Any:
        """Get payload as JSON (requires json module)"""
        import json
        return json.loads(self.payload_str)


@dataclass
class MQTTClient:
    """Represents an MQTT client connection"""
    client_id: str
    username: Optional[str] = None
    password: Optional[str] = None
    keepalive: int = 60
    clean_session: bool = True
    address: Optional[tuple] = None  # (host, port)
    
    def __str__(self) -> str:
        return f"MQTTClient(id={self.client_id}, user={self.username})"
