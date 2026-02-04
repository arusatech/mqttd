#!/usr/bin/env python3
"""
Tests for MQTT 5.0 OASIS specification compliance rejection cases.

Covers:
- CONNECT reserved flag (bit 0) MUST be 0 → CONNACK 0x81 (Malformed Packet), connection closed
- SUBSCRIBE reserved bits (6 and 7) and Retain Handling 3 → DISCONNECT 0x81 (Malformed Packet)
- No Local on shared subscription → filter rejected with 0x83 (Implementation Specific Error) in SUBACK
"""

import sys
import os
import asyncio
import struct
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mqttd import MQTTApp, MQTTProtocol, MQTTMessageType, MQTT5Protocol
from mqttd.reason_codes import ReasonCode


# CONNECT variable header: protocol name (2+4=6), level (1), connect_flags (1) → flags at index 7
CONNECT_FLAGS_OFFSET_IN_PAYLOAD = 7


def _malformed_connect_with_reserved_flag():
    """Build a MQTT 5.0 CONNECT with reserved bit (bit 0) set. Server must respond with CONNACK 0x81."""
    connect_bytes = MQTT5Protocol.build_connect_v5("test-client", keepalive=60)
    rl, n = MQTTProtocol.decode_remaining_length(connect_bytes, 1)
    payload = connect_bytes[1 + n : 1 + n + rl]
    # Set reserved bit (bit 0) in connect flags
    flags_byte = payload[CONNECT_FLAGS_OFFSET_IN_PAYLOAD] | 0x01
    new_payload = payload[:CONNECT_FLAGS_OFFSET_IN_PAYLOAD] + bytes([flags_byte]) + payload[CONNECT_FLAGS_OFFSET_IN_PAYLOAD + 1 :]
    return connect_bytes[: 1 + n] + new_payload


def _build_subscribe_v5_payload(packet_id: int, topic: str, options_byte: int) -> bytes:
    """Build SUBSCRIBE variable header + payload (no properties). Options byte is raw (can be malformed)."""
    payload = struct.pack(">H", packet_id)
    payload += b"\x00"  # properties length 0
    payload += MQTTProtocol.encode_string(topic)
    payload += bytes([options_byte])
    return payload


def _build_subscribe_v5_packet(packet_id: int, topic: str, options_byte: int) -> bytes:
    """Full SUBSCRIBE packet (fixed header + variable + payload)."""
    sub_payload = _build_subscribe_v5_payload(packet_id, topic, options_byte)
    msg_type = MQTTMessageType.SUBSCRIBE | 0x02  # QoS 1
    rl_bytes = MQTTProtocol.encode_remaining_length(len(sub_payload))
    return bytes([msg_type]) + rl_bytes + sub_payload


@pytest.mark.asyncio
async def test_connect_reserved_flag_rejected():
    """CONNECT with reserved flag set → CONNACK 0x81 (Malformed Packet), connection closed."""
    app = MQTTApp(port=18991)
    server_task = asyncio.create_task(app._start_server())
    await asyncio.sleep(0.1)
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", 18991)
        connect_bad = _malformed_connect_with_reserved_flag()
        writer.write(connect_bad)
        await writer.drain()
        # CONNACK: fixed header (1 + 1..4 bytes rl), then variable: flags (1), reason_code (1), [properties]
        header = await reader.readexactly(2)
        assert header[0] == MQTTMessageType.CONNACK
        rl, rl_len = MQTTProtocol.decode_remaining_length(header, 1)
        if rl_len > 1:
            extra = await reader.readexactly(rl_len - 1)
            rl, _ = MQTTProtocol.decode_remaining_length(header + extra, 1)
        var_header = await reader.readexactly(rl)
        # Variable header: connack_flags (1), reason_code (1)
        reason_code = var_header[1]
        assert reason_code == ReasonCode.MALFORMED_PACKET, f"expected 0x81, got {hex(reason_code)}"
        writer.close()
        await writer.wait_closed()
    finally:
        app._running = False
        await asyncio.sleep(0.05)
        server_task.cancel()
        try:
            await server_task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_subscribe_reserved_bits_rejected():
    """SUBSCRIBE with reserved bits (6 or 7) set → DISCONNECT 0x81, connection closed."""
    app = MQTTApp(port=18992)
    server_task = asyncio.create_task(app._start_server())
    await asyncio.sleep(0.1)
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", 18992)
        # Valid MQTT 5.0 CONNECT
        writer.write(MQTT5Protocol.build_connect_v5("sub-client", keepalive=60))
        await writer.drain()
        connack = await reader.readexactly(2)
        assert connack[0] == MQTTMessageType.CONNACK
        rl, rl_n = MQTTProtocol.decode_remaining_length(connack, 1)
        if rl_n > 1:
            connack += await reader.readexactly(rl_n - 1)
            rl, _ = MQTTProtocol.decode_remaining_length(connack, 1)
        await reader.readexactly(rl)
        # SUBSCRIBE with options byte 0xC0 (reserved bits 6 and 7 set)
        sub_bad = _build_subscribe_v5_packet(1, "a/b", 0xC0)
        writer.write(sub_bad)
        await writer.drain()
        # Expect DISCONNECT (0xE0), reason code 0x81
        disc_header = await reader.readexactly(2)
        assert disc_header[0] == MQTTMessageType.DISCONNECT
        rl, _ = MQTTProtocol.decode_remaining_length(disc_header, 1)
        disc_var = await reader.readexactly(rl)
        assert disc_var[0] == ReasonCode.MALFORMED_PACKET, f"expected 0x81, got {hex(disc_var[0])}"
        writer.close()
        await writer.wait_closed()
    finally:
        app._running = False
        await asyncio.sleep(0.05)
        server_task.cancel()
        try:
            await server_task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_subscribe_retain_handling_3_rejected():
    """SUBSCRIBE with Retain Handling 3 → DISCONNECT 0x81 (Malformed Packet)."""
    app = MQTTApp(port=18993)
    server_task = asyncio.create_task(app._start_server())
    await asyncio.sleep(0.1)
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", 18993)
        writer.write(MQTT5Protocol.build_connect_v5("sub-client-2", keepalive=60))
        await writer.drain()
        connack = await reader.readexactly(2)
        assert connack[0] == MQTTMessageType.CONNACK
        rl, rl_n = MQTTProtocol.decode_remaining_length(connack, 1)
        if rl_n > 1:
            connack += await reader.readexactly(rl_n - 1)
            rl, _ = MQTTProtocol.decode_remaining_length(connack, 1)
        await reader.readexactly(rl)
        # Options byte: retain_handling in bits 4-5, value 3 is invalid. 0x30 = 3<<4
        sub_bad = _build_subscribe_v5_packet(1, "x/y", 0x30)
        writer.write(sub_bad)
        await writer.drain()
        disc_header = await reader.readexactly(2)
        assert disc_header[0] == MQTTMessageType.DISCONNECT
        rl, _ = MQTTProtocol.decode_remaining_length(disc_header, 1)
        disc_var = await reader.readexactly(rl)
        assert disc_var[0] == ReasonCode.MALFORMED_PACKET
        writer.close()
        await writer.wait_closed()
    finally:
        app._running = False
        await asyncio.sleep(0.05)
        server_task.cancel()
        try:
            await server_task
        except asyncio.CancelledError:
            pass


@pytest.mark.asyncio
async def test_no_local_on_shared_subscription_rejected():
    """No Local on shared subscription → that filter gets 0x83 (Implementation Specific Error) in SUBACK."""
    app = MQTTApp(port=18994)
    server_task = asyncio.create_task(app._start_server())
    await asyncio.sleep(0.1)
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", 18994)
        writer.write(MQTT5Protocol.build_connect_v5("shared-client", keepalive=60))
        await writer.drain()
        connack = await reader.readexactly(2)
        assert connack[0] == MQTTMessageType.CONNACK
        rl, rl_n = MQTTProtocol.decode_remaining_length(connack, 1)
        if rl_n > 1:
            connack += await reader.readexactly(rl_n - 1)
            rl, _ = MQTTProtocol.decode_remaining_length(connack, 1)
        await reader.readexactly(rl)
        # SUBSCRIBE: $share/grp/topic with no_local=1 (bit 2 = 0x04)
        sub_payload = _build_subscribe_v5_payload(1, "$share/grp/topic", 0x04)
        sub_pkt = bytes([MQTTMessageType.SUBSCRIBE | 0x02]) + MQTTProtocol.encode_remaining_length(len(sub_payload)) + sub_payload
        writer.write(sub_pkt)
        await writer.drain()
        # SUBACK: one reason code = 0x83 (IMPLEMENTATION_SPECIFIC_ERROR_SUB)
        suback_header = await reader.readexactly(2)
        assert suback_header[0] == MQTTMessageType.SUBACK
        rl, _ = MQTTProtocol.decode_remaining_length(suback_header, 1)
        suback_rest = await reader.readexactly(rl)
        packet_id = struct.unpack(">H", suback_rest[:2])[0]
        assert packet_id == 1
        # Skip variable header (packet_id + prop length + props), reason codes are at the end
        offset = 2
        prop_len, plen_n = MQTTProtocol.decode_remaining_length(suback_rest, offset)
        offset += plen_n + prop_len
        reason_codes = list(suback_rest[offset:])
        assert len(reason_codes) == 1
        assert reason_codes[0] == ReasonCode.IMPLEMENTATION_SPECIFIC_ERROR_SUB, f"expected 0x83, got {hex(reason_codes[0])}"
        writer.close()
        await writer.wait_closed()
    finally:
        app._running = False
        await asyncio.sleep(0.05)
        server_task.cancel()
        try:
            await server_task
        except asyncio.CancelledError:
            pass


def main():
    """Run all OASIS compliance rejection tests."""
    print("MQTT 5.0 OASIS compliance rejection tests")
    print("=" * 50)
    try:
        asyncio.run(test_connect_reserved_flag_rejected())
        print("  ✓ CONNECT reserved flag → CONNACK 0x81")
        asyncio.run(test_subscribe_reserved_bits_rejected())
        print("  ✓ SUBSCRIBE reserved bits → DISCONNECT 0x81")
        asyncio.run(test_subscribe_retain_handling_3_rejected())
        print("  ✓ SUBSCRIBE Retain Handling 3 → DISCONNECT 0x81")
        asyncio.run(test_no_local_on_shared_subscription_rejected())
        print("  ✓ No Local on shared subscription → SUBACK 0x83")
        print("=" * 50)
        print("All OASIS compliance rejection tests passed.")
        return 0
    except AssertionError as e:
        print(f"FAIL: {e}")
        import traceback
        traceback.print_exc()
        return 1
    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
