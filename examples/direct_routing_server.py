#!/usr/bin/env python3
"""
MQTT Server with Direct Client Routing (No Redis)

This example demonstrates an MQTT server using direct in-memory routing
between clients - no Redis required. Lower latency for single-server deployments.
"""

import asyncio
import logging
from mqttd import MQTTApp, MQTTMessage, MQTTClient

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

# Create MQTT app WITHOUT Redis - uses direct routing
# This is simpler and has lower latency for single-server deployments
app = MQTTApp(
    port=1883,
    use_redis=False  # Direct routing - no Redis needed!
)

@app.subscribe("sensors/temperature")
async def handle_temperature_subscribe(topic: str, client: MQTTClient):
    """Handle subscription to temperature topic"""
    print(f"Client {client.client_id} subscribed to {topic}")
    # Messages will be directly routed to this client when published

@app.subscribe("sensors/humidity")
async def handle_humidity_subscribe(topic: str, client: MQTTClient):
    """Handle subscription to humidity topic"""
    print(f"Client {client.client_id} subscribed to {topic}")

@app.publish_handler("sensors/+")
async def handle_sensor_publish(message: MQTTMessage, client: MQTTClient):
    """Handle incoming PUBLISH messages - directly routed to subscribers"""
    print(f"Received PUBLISH from {client.client_id}")
    print(f"  Topic: {message.topic}")
    print(f"  Payload: {message.payload_str}")
    # Message is automatically routed directly to subscribed clients (no Redis)

@app.publish_handler()
async def handle_all_publishes(message: MQTTMessage, client: MQTTClient):
    """Handle all incoming PUBLISH messages"""
    print(f"PUBLISH received on {message.topic} (direct routing)")

if __name__ == "__main__":
    print("Starting MQTT server with DIRECT ROUTING (no Redis)...")
    print("Features:")
    print("  - Direct in-memory message routing")
    print("  - Lower latency (no Redis network hop)")
    print("  - Simpler setup (no external dependencies)")
    print("  - Perfect for single-server deployments")
    print("\nPress Ctrl+C to stop")
    
    try:
        app.run()
    except KeyboardInterrupt:
        print("\nServer stopped")
