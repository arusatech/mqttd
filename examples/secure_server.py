#!/usr/bin/env python3
"""
Secure MQTT (MQTTS) Server Example

This example demonstrates an MQTTS server with TLS/SSL support.
"""

import ssl
from mqttd import MQTTApp, MQTTMessage, MQTTClient

# Create SSL context for MQTTS
# Note: You need to generate certificates for production use
ssl_context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)

# For testing, you can use self-signed certificates:
# ssl_context.check_hostname = False
# ssl_context.verify_mode = ssl.CERT_NONE

# Load certificate and key (uncomment when you have them)
# ssl_context.load_cert_chain('server.crt', 'server.key')

# Create the MQTTS application
app = MQTTApp(port=8883, ssl_context=ssl_context)

@app.subscribe("secure/data")
async def handle_secure_subscribe(topic: str, client: MQTTClient):
    """Handle secure subscription"""
    print(f"Secure client {client.client_id} subscribed to {topic}")
    return b"Secure data payload"

@app.publish_handler("secure/+")
async def handle_secure_publish(message: MQTTMessage, client: MQTTClient):
    """Handle secure PUBLISH messages"""
    print(f"Secure PUBLISH from {client.client_id}: {message.payload_str}")

if __name__ == "__main__":
    print("Starting MQTTS server on port 8883...")
    print("Press Ctrl+C to stop")
    try:
        app.run()
    except KeyboardInterrupt:
        print("\nServer stopped")
