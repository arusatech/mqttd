#!/usr/bin/env python3
"""
MQTT 5.0 Server Example

This example demonstrates an MQTT server with MQTT 5.0 support.
The server automatically detects and handles both MQTT 3.1.1 and MQTT 5.0 clients.
"""

import logging
from mqttd import MQTTApp, MQTTMessage, MQTTClient

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

# Create MQTT app (supports both MQTT 3.1.1 and 5.0)
app = MQTTApp(port=1883)

@app.subscribe("sensors/temperature")
async def handle_temperature_subscribe(topic: str, client: MQTTClient):
    """Handle subscription to temperature topic"""
    protocol_version = getattr(client, '_protocol_version', 4)
    mqtt_version = "MQTT 5.0" if protocol_version == 5 else "MQTT 3.1.1"
    print(f"Client {client.client_id} ({mqtt_version}) subscribed to {topic}")

@app.subscribe("sensors/humidity")
async def handle_humidity_subscribe(topic: str, client: MQTTClient):
    """Handle subscription to humidity topic"""
    print(f"Client {client.client_id} subscribed to {topic}")

@app.publish_handler("sensors/+")
async def handle_sensor_publish(message: MQTTMessage, client: MQTTClient):
    """Handle incoming PUBLISH messages"""
    print(f"Received PUBLISH from {client.client_id}")
    print(f"  Topic: {message.topic}")
    print(f"  Payload: {message.payload_str}")
    print(f"  QoS: {message.qos}")

if __name__ == "__main__":
    print("Starting MQTT server with MQTT 5.0 support...")
    print("Features:")
    print("  - Automatic protocol version detection (3.1.1 and 5.0)")
    print("  - Reason codes in all ACK packets (MQTT 5.0)")
    print("  - Properties support (MQTT 5.0)")
    print("  - Backward compatible with MQTT 3.1.1 clients")
    print("\nPress Ctrl+C to stop")
    
    try:
        app.run()
    except KeyboardInterrupt:
        print("\nServer stopped")
