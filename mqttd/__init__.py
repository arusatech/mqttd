"""
MQTTD - FastAPI-like MQTT/MQTTS Server for Python

A Python package for creating MQTT and MQTTS servers with a FastAPI-like
decorator-based API, compatible with libcurl clients.
Supports both MQTT 3.1.1 and MQTT 5.0.
"""

from .app import MQTTApp
from .decorators import subscribe, publish_handler
from .types import MQTTMessage, MQTTClient, QoS
from .protocol import MQTTProtocol, MQTTMessageType, MQTTConnAckCode
from .protocol_v5 import MQTT5Protocol
from .properties import PropertyEncoder, PropertyType
from .reason_codes import ReasonCode

__version__ = "0.3.1"  # MQTT 5.0 support added
__all__ = [
    "MQTTApp", 
    "subscribe", 
    "publish_handler", 
    "MQTTMessage", 
    "MQTTClient",
    "QoS",
    "MQTTProtocol",
    "MQTTMessageType",
    "MQTTConnAckCode",
    # MQTT 5.0 exports
    "MQTT5Protocol",
    "PropertyEncoder",
    "PropertyType",
    "ReasonCode",
]
