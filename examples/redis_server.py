#!/usr/bin/env python3
"""
MQTT Server with Redis Pub/Sub Backend Example

This example demonstrates an MQTT server using Redis pub/sub as the backend
for low-latency message routing (no database).
"""

import asyncio
import logging
from mqttd import MQTTApp, MQTTMessage, MQTTClient

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

# Create MQTT app with Redis backend
# Redis will be used for pub/sub - no database, optimized for latency
app = MQTTApp(
    port=1883,
    redis_host="localhost",  # Redis server host
    redis_port=6379,         # Redis server port
    # redis_password="your_password",  # Optional
    # redis_url="redis://localhost:6379/0"  # Or use URL instead
)

@app.subscribe("sensors/temperature")
async def handle_temperature_subscribe(topic: str, client: MQTTClient):
    """Handle subscription to temperature topic"""
    print(f"Client {client.client_id} subscribed to {topic}")
    # Messages will be automatically forwarded from Redis to this client

@app.subscribe("sensors/humidity")
async def handle_humidity_subscribe(topic: str, client: MQTTClient):
    """Handle subscription to humidity topic"""
    print(f"Client {client.client_id} subscribed to {topic}")

@app.publish_handler("sensors/+")
async def handle_sensor_publish(message: MQTTMessage, client: MQTTClient):
    """Handle incoming PUBLISH messages - automatically published to Redis"""
    print(f"Received PUBLISH from {client.client_id}")
    print(f"  Topic: {message.topic}")
    print(f"  Payload: {message.payload_str}")
    # Message is automatically published to Redis channel

@app.publish_handler()
async def handle_all_publishes(message: MQTTMessage, client: MQTTClient):
    """Handle all incoming PUBLISH messages"""
    print(f"PUBLISH received on {message.topic} (forwarded to Redis)")

if __name__ == "__main__":
    print("Starting MQTT server with Redis pub/sub backend...")
    print("Features:")
    print("  - Low latency message routing via Redis pub/sub")
    print("  - No database - pure in-memory pub/sub")
    print("  - Automatic message forwarding between MQTT clients")
    print("\nMake sure Redis is running on localhost:6379")
    print("Press Ctrl+C to stop")
    
    try:
        app.run()
    except KeyboardInterrupt:
        print("\nServer stopped")
