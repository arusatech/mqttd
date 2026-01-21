"""
MQTT over QUIC Server Example

This example demonstrates how to run an MQTT server with QUIC/HTTP3 support.
QUIC provides lower latency connection setup and better performance in
lossy networks compared to TCP.

Requirements:
- aioquic: pip install aioquic
- TLS certificates for QUIC (QUIC requires TLS 1.3)

Note: You need to generate TLS certificates. For testing, you can use:
  openssl req -x509 -newkey rsa:2048 -nodes -keyout key.pem -out cert.pem -days 365
"""

import ssl
from mqttd import MQTTApp, MQTTMessage, MQTTClient

# Create MQTT app with QUIC enabled
app = MQTTApp(
    port=1883,  # TCP port
    enable_quic=True,  # Enable QUIC transport
    quic_port=1884,  # UDP port for QUIC
    quic_certfile="cert.pem",  # TLS certificate (required for QUIC)
    quic_keyfile="key.pem",  # TLS private key (required for QUIC)
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
    print("Starting MQTT server with QUIC support...")
    print("TCP: mqtt://localhost:1883")
    print("QUIC: quic://localhost:1884")
    print("\nNote: QUIC requires TLS certificates (cert.pem and key.pem)")
    app.run()
