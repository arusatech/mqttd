#!/usr/bin/env python3
"""
MQTT Server with Configuration File Example

This example demonstrates using a configuration file similar to the C reference.
"""

from mqttd import MQTTApp, MQTTMessage, MQTTClient

# Create app with configuration file
app = MQTTApp(port=1883, config_file="mqttd.config")

@app.subscribe("test/topic")
async def handle_test(topic: str, client: MQTTClient):
    """Handle test topic subscription"""
    print(f"Test subscription: {topic}")
    return b"Test payload"

if __name__ == "__main__":
    print("Starting MQTT server with config file...")
    print("Configuration options:")
    print("  - version: MQTT protocol version")
    print("  - PUBLISH-before-SUBACK: Send PUBLISH before SUBACK")
    print("  - short-PUBLISH: Truncate PUBLISH messages")
    print("  - error-CONNACK: Set CONNACK return code")
    print("  - excessive-remaining: Invalid remaining length")
    print("  - Testnum: Test number")
    print("\nPress Ctrl+C to stop")
    try:
        app.run()
    except KeyboardInterrupt:
        print("\nServer stopped")
