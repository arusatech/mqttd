#!/usr/bin/env python3
"""
MQTT over QUIC Server - QUIC-Only Mode

This example demonstrates an MQTT server running ONLY on QUIC/ngtcp2,
with TCP transport disabled.

Requirements:
- ngtcp2 C library (for production-grade QUIC)
- TLS certificates for QUIC (QUIC requires TLS 1.3)

Note: You need to generate TLS certificates. For testing, you can use:
  openssl req -x509 -newkey rsa:2048 -nodes -keyout key.pem -out cert.pem -days 365
"""

import logging
from mqttd import MQTTApp, MQTTMessage, MQTTClient

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

# Create MQTT app with QUIC-only mode (TCP disabled)
app = MQTTApp(
    enable_tcp=False,  # Disable TCP transport
    enable_quic=True,   # Enable QUIC transport
    quic_port=1884,    # UDP port for QUIC
    quic_certfile="cert.pem",  # TLS certificate (required for QUIC)
    quic_keyfile="key.pem",    # TLS private key (required for QUIC)
)

@app.subscribe("sensors/#")
async def handle_sensor(topic: str, client: MQTTClient):
    """Handle sensor messages"""
    print(f"[{client.client_id}] Subscribed to {topic}")

@app.publish_handler("sensors/temperature")
async def handle_temperature(message: MQTTMessage, client: MQTTClient):
    """Handle temperature publishes"""
    print(f"Temperature from {client.client_id}: {message.payload_str}")

if __name__ == "__main__":
    print("Starting MQTT server in QUIC-only mode...")
    print("QUIC: quic://localhost:1884")
    print("\nNote: QUIC requires TLS certificates (cert.pem and key.pem)")
    print("This server will NOT accept TCP connections.")
    app.run()
