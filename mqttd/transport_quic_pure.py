"""
MQTT over QUIC Transport - Pure Python Implementation
Based on curl's vquic.c reference implementation
Compatible with no-GIL Python (no Limited API)

This is a simplified QUIC implementation for MQTT.
For production, use transport_quic_ngtcp2.py with ngtcp2.
"""

import asyncio
import logging
import socket
import struct
import os
from typing import Optional, Dict, Callable, Any, Tuple, Set
from dataclasses import dataclass, field
from threading import Lock
import time

logger = logging.getLogger(__name__)


@dataclass
class QUICPacket:
    """Represents a QUIC packet"""
    version: int
    dcid: bytes  # Destination Connection ID
    scid: bytes  # Source Connection ID
    packet_number: int
    payload: bytes
    packet_type: int = 0  # 0=Initial, 1=Handshake, 2=1-RTT


class QUICConnection:
    """
    QUIC Connection - single connection over UDP
    
    Based on curl's cf_quic_ctx and cf_ngtcp2_ctx structures
    """
    
    def __init__(self, dcid: bytes, scid: bytes, remote_addr: Tuple[str, int]):
        self.dcid = dcid  # Destination Connection ID (our side)
        self.scid = scid  # Source Connection ID (client side)
        self.remote_addr = remote_addr
        self.state = "handshake"  # handshake, connected, closed
        self.streams: Dict[int, 'QUICStream'] = {}
        self.next_stream_id = 0
        self.lock = Lock()  # Thread-safe for no-GIL
        self.created_at = time.time()
        self.last_packet_at = time.time()
        self.version = 1  # QUIC version
        self.handshake_complete = False
        
    def create_stream(self) -> int:
        """Create a new bidirectional stream (even numbers for client, odd for server)"""
        with self.lock:
            stream_id = self.next_stream_id
            self.next_stream_id += 4  # Bidirectional streams: 0, 4, 8, ...
            self.streams[stream_id] = QUICStream(stream_id, self)
            return stream_id
    
    def get_stream(self, stream_id: int) -> Optional['QUICStream']:
        """Get stream by ID"""
        with self.lock:
            return self.streams.get(stream_id)
    
    def update_activity(self):
        """Update last packet time"""
        self.last_packet_at = time.time()
    
    def is_expired(self, timeout: int = 60) -> bool:
        """Check if connection expired"""
        return (time.time() - self.last_packet_at) > timeout


class QUICStream:
    """
    QUIC Stream - single bidirectional stream
    
    Similar to curl's h3_stream_ctx but for MQTT
    """
    
    def __init__(self, stream_id: int, connection: QUICConnection):
        self.stream_id = stream_id
        self.connection = connection
        self.buffer = bytearray()
        self.closed = False
        self.finished = False
        self.lock = Lock()  # Thread-safe for no-GIL
    
    def append_data(self, data: bytes):
        """Append data to stream buffer"""
        with self.lock:
            self.buffer.extend(data)
    
    def read(self, n: int = -1) -> bytes:
        """Read data from stream"""
        with self.lock:
            if n == -1:
                data = bytes(self.buffer)
                self.buffer.clear()
                return data
            data = bytes(self.buffer[:n])
            self.buffer = self.buffer[n:]
            return data
    
    def has_data(self) -> bool:
        """Check if stream has data"""
        with self.lock:
            return len(self.buffer) > 0


class QUICServerProtocol(asyncio.DatagramProtocol):
    """UDP protocol handler for QUIC (based on curl's packet handling)"""
    
    def __init__(self, server: 'QUICServer'):
        self.server = server
    
    def datagram_received(self, data: bytes, addr: Tuple[str, int]):
        """Handle incoming UDP datagram"""
        self.server.handle_packet(data, addr)
    
    def error_received(self, exc: Exception):
        """Handle UDP error"""
        logger.error(f"UDP error: {exc}")
    
    def connection_lost(self, exc: Optional[Exception]):
        """Handle connection loss"""
        if exc:
            logger.error(f"UDP connection lost: {exc}")


class QUICStreamReader:
    """Reader interface for QUIC stream (compatible with asyncio.StreamReader)"""
    
    def __init__(self, stream: QUICStream):
        self.stream = stream
    
    async def read(self, n: int = -1) -> bytes:
        """Read data from stream"""
        while len(self.stream.buffer) == 0 and not self.stream.closed:
            await asyncio.sleep(0.01)
        with self.stream.lock:
            if n == -1:
                data = bytes(self.stream.buffer)
                self.stream.buffer.clear()
                return data
            data = bytes(self.stream.buffer[:n])
            self.stream.buffer = self.stream.buffer[n:]
            return data
    
    async def readexactly(self, n: int) -> bytes:
        """Read exactly n bytes"""
        data = b''
        while len(data) < n and not self.stream.closed:
            chunk = await self.read(n - len(data))
            if not chunk:
                raise EOFError("Stream closed")
            data += chunk
        return data


class QUICStreamWriter:
    """Writer interface for QUIC stream (compatible with asyncio.StreamWriter)"""
    
    def __init__(self, connection: QUICConnection, stream: QUICStream, server: 'QUICServer'):
        self.connection = connection
        self.stream = stream
        self.server = server
    
    def write(self, data: bytes):
        """Write data to QUIC stream"""
        # Build QUIC packet with MQTT data
        packet = self.server._build_quic_packet(self.connection, self.stream, data)
        self.server.send_packet(packet, self.connection.remote_addr)
    
    async def drain(self):
        """Drain send buffer"""
        await asyncio.sleep(0)  # Yield to event loop
    
    def close(self):
        """Close stream"""
        self.stream.closed = True
    
    async def wait_closed(self):
        """Wait for stream to close"""
        while not self.stream.closed:
            await asyncio.sleep(0.01)
    
    def get_extra_info(self, name: str):
        """Get extra connection info"""
        if name == 'peername':
            return self.connection.remote_addr
        elif name == 'socket':
            return self.connection
        return None


class QUICServer:
    """
    MQTT over QUIC Server using UDP sockets
    
    Based on curl's QUIC implementation patterns:
    - UDP socket for packet I/O (like curl's sockfd)
    - Connection tracking by Connection ID (like curl's connection hash)
    - Stream management (like curl's stream hash)
    - Async packet processing (like curl's recvmmsg/recvmsg)
    """
    
    # Constants from curl's vquic_int.h
    MAX_PKT_BURST = 10
    MAX_UDP_PAYLOAD_SIZE = 1452
    NW_CHUNK_SIZE = 64 * 1024
    
    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 1884,
        certfile: Optional[str] = None,
        keyfile: Optional[str] = None,
    ):
        self.host = host
        self.port = port
        self.certfile = certfile
        self.keyfile = keyfile
        
        # UDP socket (like curl's cf_quic_ctx.sockfd)
        self.sock: Optional[socket.socket] = None
        self.transport: Optional[asyncio.DatagramTransport] = None
        
        # Connection tracking by Destination Connection ID (like curl's connection hash)
        self.connections: Dict[bytes, QUICConnection] = {}
        self.connections_lock = Lock()  # Thread-safe for no-GIL
        
        # MQTT handler
        self.mqtt_handler: Optional[Callable] = None
        
        # Statistics (like curl's debugging)
        self.packets_received = 0
        self.packets_sent = 0
        
        # Connection cleanup
        self._cleanup_task: Optional[asyncio.Task] = None
    
    def set_mqtt_handler(self, handler: Callable):
        """Set MQTT connection handler"""
        self.mqtt_handler = handler
    
    async def start(self):
        """Start QUIC server on UDP socket (like curl's server binding)"""
        loop = asyncio.get_event_loop()
        
        # Create UDP socket (like curl's vquic_ctx_init)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        
        # Enable socket options for better performance (like curl's GSO)
        try:
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024 * 1024)  # 1MB
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1024 * 1024)  # 1MB
        except Exception:
            pass
        
        self.sock.bind((self.host, self.port))
        self.sock.setblocking(False)
        
        # Create datagram transport (like curl's packet receive loop)
        self.transport, protocol = await loop.create_datagram_endpoint(
            lambda: QUICServerProtocol(self),
            sock=self.sock
        )
        
        # Start cleanup task
        self._cleanup_task = asyncio.create_task(self._cleanup_expired_connections())
        
        logger.info(f"MQTT over QUIC server listening on {self.host}:{self.port} (UDP)")
    
    async def stop(self):
        """Stop QUIC server"""
        if self._cleanup_task:
            self._cleanup_task.cancel()
        
        if self.transport:
            self.transport.close()
        if self.sock:
            self.sock.close()
        logger.info("QUIC server stopped")
    
    async def _cleanup_expired_connections(self):
        """Clean up expired connections periodically (like curl's timeout handling)"""
        while True:
            try:
                await asyncio.sleep(30)  # Check every 30 seconds
                with self.connections_lock:
                    expired = [
                        dcid for dcid, conn in self.connections.items()
                        if conn.is_expired()
                    ]
                    for dcid in expired:
                        del self.connections[dcid]
                        logger.debug(f"Cleaned up expired QUIC connection: {dcid.hex()[:8]}")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in connection cleanup: {e}")
    
    def handle_packet(self, data: bytes, addr: Tuple[str, int]):
        """Handle incoming QUIC packet (like curl's vquic_recv_packets)"""
        try:
            # Parse QUIC packet header (simplified - real QUIC is more complex)
            if len(data) < 20:
                return
            
            # Extract Connection ID (simplified parsing)
            # Real QUIC has variable-length connection IDs in packet header
            # This is a simplified version for MQTT-over-QUIC
            dcid = self._extract_dcid(data)
            if not dcid:
                return
            
            # Get or create connection (like curl's connection lookup)
            connection = self.get_or_create_connection(dcid, addr)
            connection.update_activity()
            
            # Process packet (like curl's packet processing loop)
            asyncio.create_task(self._process_quic_packet(connection, data, addr))
            
            self.packets_received += 1
            
        except Exception as e:
            logger.error(f"Error handling QUIC packet: {e}")
    
    def _extract_dcid(self, data: bytes) -> Optional[bytes]:
        """Extract Destination Connection ID from QUIC packet (simplified)"""
        # Simplified: assume DCID is at offset 8, 8 bytes long
        # Real QUIC parsing is more complex with variable-length fields
        if len(data) < 16:
            return None
        return data[8:16]  # 8-byte DCID
    
    def get_or_create_connection(self, dcid: bytes, addr: Tuple[str, int]) -> QUICConnection:
        """Get existing connection or create new one (like curl's connection management)"""
        with self.connections_lock:
            if dcid in self.connections:
                return self.connections[dcid]
            
            # Create new connection (like curl's connection setup)
            scid = os.urandom(8)  # Generate random Source Connection ID
            connection = QUICConnection(dcid, scid, addr)
            self.connections[dcid] = connection
            logger.debug(f"New QUIC connection: DCID={dcid.hex()[:8]}, addr={addr}")
            return connection
    
    async def _process_quic_packet(self, connection: QUICConnection, data: bytes, addr: Tuple[str, int]):
        """Process QUIC packet and extract MQTT data (like curl's packet processing)"""
        try:
            # Simplified QUIC processing
            # Real QUIC would need proper packet parsing, frame extraction, etc.
            
            # For MQTT-over-QUIC in single-stream mode:
            # - Use stream ID 0 for the main MQTT connection
            # - Extract payload from QUIC frames
            
            stream_id = 0  # Single stream mode for MQTT
            
            if connection.state == "handshake":
                # Initial handshake packet - create stream 0
                if stream_id not in connection.streams:
                    connection.streams[stream_id] = QUICStream(stream_id, connection)
                stream = connection.streams[stream_id]
                
                # Append data (handshake + potential early MQTT data)
                stream.append_data(data)
                
                # Once we receive a complete handshake, switch to connected
                # Simplified: assume connected after first packet
                connection.state = "connected"
                connection.handshake_complete = True
                logger.debug(f"QUIC handshake complete for connection {connection.dcid.hex()[:8]}")
            
            elif connection.state == "connected":
                # Extract payload (skip QUIC headers - simplified)
                # Real implementation would parse QUIC frames properly
                if len(data) > 20:
                    payload = data[20:]  # Skip header
                    stream = connection.get_stream(stream_id)
                    if not stream:
                        stream = connection.create_stream()
                    
                    stream.append_data(payload)
                    
                    # If we have MQTT handler and data, process it
                    if self.mqtt_handler and stream.has_data():
                        await self._handle_mqtt_over_quic(connection, stream)
            
        except Exception as e:
            logger.error(f"Error processing QUIC packet: {e}")
    
    async def _handle_mqtt_over_quic(self, connection: QUICConnection, stream: QUICStream):
        """Handle MQTT data received over QUIC stream (like curl's stream processing)"""
        if not self.mqtt_handler:
            return
        
        try:
            # Create reader/writer-like interface for MQTT handler
            # This allows reusing existing MQTT handling code
            reader = QUICStreamReader(stream)
            writer = QUICStreamWriter(connection, stream, self)
            
            # Call MQTT handler (same interface as TCP: reader, writer only)
            # Connection available via writer.get_extra_info('socket')
            await self.mqtt_handler(reader, writer)
            
        except Exception as e:
            logger.error(f"Error handling MQTT over QUIC: {e}")
    
    def send_packet(self, data: bytes, addr: Tuple[str, int]):
        """Send QUIC packet via UDP (like curl's do_sendmsg/vquic_send)"""
        if self.transport:
            try:
                self.transport.sendto(data, addr)
                self.packets_sent += 1
            except Exception as e:
                logger.error(f"Error sending QUIC packet: {e}")
    
    def _build_quic_packet(self, connection: QUICConnection, stream: QUICStream, payload: bytes) -> bytes:
        """Build QUIC packet with MQTT payload (simplified packet construction)"""
        # Simplified QUIC packet format for MQTT-over-QUIC
        # Real QUIC has complex headers with variable-length fields, flags, etc.
        
        version = connection.version
        dcid = connection.scid  # Destination = client's source
        scid = connection.dcid  # Source = our destination
        packet_number = 0  # Simplified: use 0 for now
        
        # Build packet header (simplified format)
        # Real QUIC: Long Header (Initial/Handshake) or Short Header (1-RTT)
        header = bytearray()
        header.append(0xC0)  # Long header + Initial packet (simplified)
        header.extend(struct.pack('>I', version))
        header.append(8)  # DCID length
        header.extend(dcid[:8])
        header.append(8)  # SCID length
        header.extend(scid[:8])
        header.extend(struct.pack('>I', packet_number)[1:])  # 3-byte packet number
        
        # Add payload
        packet = bytes(header) + payload
        return packet
    
    def get_stats(self) -> Dict[str, Any]:
        """Get server statistics"""
        with self.connections_lock:
            return {
                'packets_received': self.packets_received,
                'packets_sent': self.packets_sent,
                'active_connections': len(self.connections),
            }
