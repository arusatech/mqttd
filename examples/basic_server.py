#!/usr/bin/env python3
"""
Basic MQTT Server Example

This example demonstrates a simple MQTT server with topic subscriptions
and publish handlers.
"""

import asyncio
from mqttd import MQTTApp, MQTTMessage, MQTTClient

# Create the MQTT application
app = MQTTApp(port=1883)

@app.subscribe("sensors/temperature")
async def handle_temperature_subscribe(topic: str, client: MQTTClient):
    """Handle subscription to temperature topic"""
    print(f"Client {client.client_id} subscribed to {topic}")
    # Return payload to send to subscribing client
    return b"Temperature: 25.5C"

@app.subscribe("sensors/humidity")
async def handle_humidity_subscribe(topic: str, client: MQTTClient):
    """Handle subscription to humidity topic"""
    print(f"Client {client.client_id} subscribed to {topic}")
    return b"Humidity: 60%"

@app.publish_handler("sensors/+")
async def handle_sensor_publish(message: MQTTMessage, client: MQTTClient):
    """Handle incoming PUBLISH messages on sensor topics"""
    print(f"Received PUBLISH from {client.client_id}")
    print(f"  Topic: {message.topic}")
    print(f"  Payload: {message.payload_str}")
    print(f"  QoS: {message.qos}")

@app.publish_handler()
async def handle_all_publishes(message: MQTTMessage, client: MQTTClient):
    """Handle all incoming PUBLISH messages"""
    print(f"PUBLISH received on {message.topic}")

if __name__ == "__main__":
    print("Starting MQTT server on port 1883...")
    print("Press Ctrl+C to stop")
    try:
        app.run()
    except KeyboardInterrupt:
        print("\nServer stopped")
