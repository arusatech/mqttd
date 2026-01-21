"""
FastAPI-like decorators for MQTT topic subscriptions and handlers
"""

from typing import Callable, Any, Optional, Union
from functools import wraps
import asyncio


class RouteHandler:
    """Internal route handler storage"""
    def __init__(self, func: Callable, topic: str, qos: int = 0):
        self.func = func
        self.topic = topic
        self.qos = qos
        self.is_async = asyncio.iscoroutinefunction(func)


def subscribe(topic: str, qos: int = 0):
    """
    Decorator to subscribe to an MQTT topic.
    
    Usage:
        @app.subscribe("sensors/temperature")
        async def handle_temperature(message: MQTTMessage, client: MQTTClient):
            print(f"Temperature: {message.payload_str}")
    
    Args:
        topic: MQTT topic pattern (supports wildcards: + for single level, # for multi-level)
        qos: Quality of Service level (0, 1, or 2)
    """
    def decorator(func: Callable) -> Callable:
        # Store metadata on the function
        if not hasattr(func, '_mqtt_routes'):
            func._mqtt_routes = []
        func._mqtt_routes.append({
            'topic': topic,
            'qos': qos,
            'handler': func
        })
        return func
    return decorator


def publish_handler(topic: Optional[str] = None):
    """
    Decorator to handle incoming PUBLISH messages.
    
    Usage:
        @app.publish_handler()
        async def handle_all_publishes(message: MQTTMessage, client: MQTTClient):
            print(f"Received on {message.topic}: {message.payload_str}")
    
    Args:
        topic: Optional topic filter. If None, handles all PUBLISH messages.
    """
    def decorator(func: Callable) -> Callable:
        if not hasattr(func, '_mqtt_publish_handlers'):
            func._mqtt_publish_handlers = []
        func._mqtt_publish_handlers.append({
            'topic': topic,
            'handler': func
        })
        return func
    return decorator


def topic_matches(pattern: str, topic: str) -> bool:
    """
    Check if a topic matches a pattern (supports MQTT wildcards).
    
    Args:
        pattern: Topic pattern with wildcards (+ for single level, # for multi-level)
        topic: Actual topic to match
        
    Returns:
        True if topic matches pattern
    """
    if pattern == topic:
        return True
    
    # Split into levels
    pattern_parts = pattern.split('/')
    topic_parts = topic.split('/')
    
    # Handle multi-level wildcard
    if pattern_parts and pattern_parts[-1] == '#':
        # Match if topic starts with pattern (excluding #)
        pattern_prefix = '/'.join(pattern_parts[:-1])
        return topic.startswith(pattern_prefix + '/') or topic == pattern_prefix
    
    # Must have same number of levels
    if len(pattern_parts) != len(topic_parts):
        return False
    
    # Check each level
    for p, t in zip(pattern_parts, topic_parts):
        if p == '+':
            continue  # Single-level wildcard matches anything
        if p == '#':
            return True  # Multi-level wildcard (should be at end, but handle anyway)
        if p != t:
            return False
    
    return True
