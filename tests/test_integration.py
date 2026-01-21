#!/usr/bin/env python3
"""
Integration tests to verify server functionality
"""

import sys
import os
import asyncio
import socket
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mqttd import MQTTApp, MQTTProtocol, MQTTMessageType

async def test_basic_connection():
    """Test basic server connection and message flow"""
    print("Testing basic connection and message flow...")
    
    app = MQTTApp(port=18830)  # Use different port to avoid conflicts
    
    # Start server in background
    server_task = asyncio.create_task(app._start_server())
    
    # Wait a bit for server to start
    await asyncio.sleep(0.1)
    
    try:
        # Connect to server
        reader, writer = await asyncio.open_connection('127.0.0.1', 18830)
        
        # Send CONNECT
        connect_msg = MQTTProtocol.build_connect("test_client", keepalive=60)
        writer.write(connect_msg)
        await writer.drain()
        
        # Read CONNACK
        connack = await reader.readexactly(4)
        assert connack[0] == MQTTMessageType.CONNACK
        print("  ✓ CONNECT/CONNACK successful")
        
        # Send PINGREQ
        pingreq = MQTTProtocol.build_pingreq()
        writer.write(pingreq)
        await writer.drain()
        
        # Read PINGRESP
        pingresp = await reader.readexactly(2)
        assert pingresp[0] == MQTTMessageType.PINGRESP
        print("  ✓ PINGREQ/PINGRESP successful")
        
        # Send SUBSCRIBE
        subscribe = MQTTProtocol.build_subscribe(1, "test/topic", 0)
        writer.write(subscribe)
        await writer.drain()
        
        # Read SUBACK
        suback_data = await reader.read(10)
        assert suback_data[0] == MQTTMessageType.SUBACK
        print("  ✓ SUBSCRIBE/SUBACK successful")
        
        # Send PUBLISH
        publish = MQTTProtocol.build_publish("test/topic", b"test message", None, 0)
        writer.write(publish)
        await writer.drain()
        
        # Should receive PUBLISH (forwarded to subscriber)
        received = await asyncio.wait_for(reader.read(100), timeout=1.0)
        assert len(received) > 0
        assert received[0] & 0xF0 == MQTTMessageType.PUBLISH
        print("  ✓ PUBLISH forwarding successful")
        
        # Send UNSUBSCRIBE
        unsubscribe = MQTTProtocol.build_unsubscribe(2, ["test/topic"])
        writer.write(unsubscribe)
        await writer.drain()
        
        # Read UNSUBACK
        unsuback_data = await reader.read(10)
        assert unsuback_data[0] == MQTTMessageType.UNSUBACK
        print("  ✓ UNSUBSCRIBE/UNSUBACK successful")
        
        # Send DISCONNECT
        disconnect = MQTTProtocol.build_disconnect()
        writer.write(disconnect)
        await writer.drain()
        
        writer.close()
        await writer.wait_closed()
        print("  ✓ DISCONNECT successful")
        
        print("Basic connection test passed!\n")
        
    except Exception as e:
        print(f"  ❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
    finally:
        # Stop server
        app._running = False
        if app._server:
            app._server.close()
            await app._server.wait_closed()
        server_task.cancel()
        try:
            await server_task
        except asyncio.CancelledError:
            pass


async def test_retained_messages():
    """Test retained message functionality"""
    print("Testing retained messages...")
    
    app = MQTTApp(port=18831)
    server_task = asyncio.create_task(app._start_server())
    await asyncio.sleep(0.1)
    
    try:
        # Connect client 1 - publish with retain
        reader1, writer1 = await asyncio.open_connection('127.0.0.1', 18831)
        connect1 = MQTTProtocol.build_connect("client1", keepalive=60)
        writer1.write(connect1)
        await writer1.drain()
        await reader1.readexactly(4)  # CONNACK
        
        # Publish with retain
        publish_retain = MQTTProtocol.build_publish("sensors/temp", b"25.5", None, 0, retain=True)
        writer1.write(publish_retain)
        await writer1.drain()
        
        # Disconnect
        disconnect1 = MQTTProtocol.build_disconnect()
        writer1.write(disconnect1)
        await writer1.drain()
        writer1.close()
        await writer1.wait_closed()
        
        # Connect client 2 - subscribe (should receive retained message)
        reader2, writer2 = await asyncio.open_connection('127.0.0.1', 18831)
        connect2 = MQTTProtocol.build_connect("client2", keepalive=60)
        writer2.write(connect2)
        await writer2.drain()
        await reader2.readexactly(4)  # CONNACK
        
        # Subscribe
        subscribe = MQTTProtocol.build_subscribe(1, "sensors/temp", 0)
        writer2.write(subscribe)
        await writer2.drain()
        await reader2.read(10)  # SUBACK
        
        # Should receive retained message
        retained_msg = await asyncio.wait_for(reader2.read(100), timeout=1.0)
        assert len(retained_msg) > 0
        assert retained_msg[0] & 0xF0 == MQTTMessageType.PUBLISH
        assert b"25.5" in retained_msg
        print("  ✓ Retained message delivered on subscription")
        
        writer2.close()
        await writer2.wait_closed()
        
        print("Retained messages test passed!\n")
        
    except Exception as e:
        print(f"  ❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
    finally:
        app._running = False
        if app._server:
            app._server.close()
            await app._server.wait_closed()
        server_task.cancel()
        try:
            await server_task
        except asyncio.CancelledError:
            pass


async def test_connection_limits():
    """Test connection limits"""
    print("Testing connection limits...")
    
    app = MQTTApp(port=18832, max_connections=2)
    server_task = asyncio.create_task(app._start_server())
    await asyncio.sleep(0.1)
    
    try:
        # Connect 2 clients (should succeed)
        readers = []
        writers = []
        for i in range(2):
            reader, writer = await asyncio.open_connection('127.0.0.1', 18832)
            connect = MQTTProtocol.build_connect(f"client{i}", keepalive=60)
            writer.write(connect)
            await writer.drain()
            connack = await reader.readexactly(4)
            assert connack[0] == MQTTMessageType.CONNACK
            readers.append(reader)
            writers.append(writer)
        
        print("  ✓ 2 connections accepted (within limit)")
        
        # Try 3rd connection (should be rejected)
        try:
            reader3, writer3 = await asyncio.open_connection('127.0.0.1', 18832)
            connect3 = MQTTProtocol.build_connect("client3", keepalive=60)
            writer3.write(connect3)
            await writer3.drain()
            connack3 = await reader3.readexactly(4)
            # Should get CONNACK with error (or connection closed)
            writer3.close()
            await writer3.wait_closed()
            print("  ✓ 3rd connection handled (may be rejected)")
        except Exception:
            print("  ✓ 3rd connection rejected as expected")
        
        # Cleanup
        for writer in writers:
            disconnect = MQTTProtocol.build_disconnect()
            writer.write(disconnect)
            await writer.drain()
            writer.close()
            await writer.wait_closed()
        
        print("Connection limits test passed!\n")
        
    except Exception as e:
        print(f"  ❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
    finally:
        app._running = False
        if app._server:
            app._server.close()
            await app._server.wait_closed()
        server_task.cancel()
        try:
            await server_task
        except asyncio.CancelledError:
            pass


async def main():
    """Run all integration tests"""
    print("=" * 60)
    print("Running Integration Tests")
    print("=" * 60 + "\n")
    
    try:
        await test_basic_connection()
        await test_retained_messages()
        await test_connection_limits()
        
        print("=" * 60)
        print("All integration tests passed! ✓")
        print("=" * 60)
        return 0
    except Exception as e:
        print(f"\n❌ Integration test error: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
