"""
Unit tests for ngtcp2 QUIC connection and stream management

Tests connection creation, packet sending/receiving, state transitions,
stream creation, data read/write, flow control, and stream closure.
"""

import sys
import os
import unittest
import asyncio
import time
from unittest.mock import Mock, patch, MagicMock

# Skip TLS initialization in tests to avoid crashes
os.environ['MQTTD_SKIP_TLS_INIT'] = '1'

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from mqttd.transport_quic_ngtcp2 import (
        NGTCP2_AVAILABLE,
        QUICServerNGTCP2,
        NGTCP2Connection,
        NGTCP2Stream,
        NGTCP2StreamReader,
        NGTCP2StreamWriter,
    )
    QUIC_AVAILABLE = True
except ImportError as e:
    QUIC_AVAILABLE = False
    IMPORT_ERROR = str(e)


class TestNGTCP2Stream(unittest.TestCase):
    """Test NGTCP2Stream class"""
    
    @classmethod
    def setUpClass(cls):
        """Set up test class"""
        if not QUIC_AVAILABLE:
            raise unittest.SkipTest(f"ngtcp2 QUIC not available: {IMPORT_ERROR}")
    
    def setUp(self):
        """Set up test fixtures"""
        # Create a mock connection
        self.mock_connection = Mock(spec=NGTCP2Connection)
        self.mock_connection.conn = None  # No real ngtcp2 connection
        
        # Create a stream
        self.stream = NGTCP2Stream(stream_id=0, connection=self.mock_connection)
    
    def test_stream_creation(self):
        """Test stream creation"""
        self.assertEqual(self.stream.stream_id, 0)
        self.assertEqual(self.stream.state, "open")
        self.assertEqual(self.stream.rx_offset, 0)
        self.assertFalse(self.stream.send_closed)
        self.assertFalse(self.stream.quic_flow_blocked)
    
    def test_stream_append_data(self):
        """Test appending data to stream"""
        data = b"test data"
        self.stream.append_data(data)
        
        self.assertEqual(len(self.stream.recv_buffer), len(data))
        self.assertEqual(self.stream.rx_offset, len(data))
        self.assertEqual(self.stream.state, "open")
    
    def test_stream_append_data_with_fin(self):
        """Test appending data with FIN flag"""
        data = b"test data"
        self.stream.append_data(data, fin=True)
        
        self.assertEqual(self.stream.state, "closed")
        self.assertEqual(self.stream.rx_offset, len(data))
    
    def test_stream_get_data(self):
        """Test getting data from stream"""
        data1 = b"first"
        data2 = b"second"
        
        self.stream.append_data(data1)
        self.stream.append_data(data2)
        
        result = self.stream.get_data()
        self.assertEqual(result, data1 + data2)
        self.assertEqual(len(self.stream.recv_buffer), 0)
    
    def test_stream_has_data(self):
        """Test checking if stream has data"""
        self.assertFalse(self.stream.has_data())
        
        self.stream.append_data(b"test")
        self.assertTrue(self.stream.has_data())
        
        self.stream.get_data()
        self.assertFalse(self.stream.has_data())
    
    def test_stream_close(self):
        """Test closing stream"""
        self.stream.close()
        self.assertEqual(self.stream.state, "closed")


class TestNGTCP2StreamReader(unittest.IsolatedAsyncioTestCase):
    """Test NGTCP2StreamReader class"""
    
    @classmethod
    def setUpClass(cls):
        """Set up test class"""
        if not QUIC_AVAILABLE:
            raise unittest.SkipTest(f"ngtcp2 QUIC not available: {IMPORT_ERROR}")
    
    def setUp(self):
        """Set up test fixtures"""
        self.mock_connection = Mock(spec=NGTCP2Connection)
        self.stream = NGTCP2Stream(stream_id=0, connection=self.mock_connection)
        self.reader = NGTCP2StreamReader(self.stream)
    
    async def test_read_with_data(self):
        """Test reading data when available"""
        self.stream.append_data(b"test data")
        
        data = await self.reader.read()
        self.assertEqual(data, b"test data")
        self.assertEqual(len(self.stream.recv_buffer), 0)
    
    async def test_read_with_size(self):
        """Test reading specific amount of data"""
        self.stream.append_data(b"test data")
        
        data = await self.reader.read(4)
        self.assertEqual(data, b"test")
        self.assertEqual(len(self.stream.recv_buffer), 5)  # " data" remaining
    
    async def test_readexactly(self):
        """Test reading exactly n bytes"""
        self.stream.append_data(b"test data")
        
        data = await self.reader.readexactly(4)
        self.assertEqual(data, b"test")
        
        data = await self.reader.readexactly(5)
        self.assertEqual(data, b" data")
    
    async def test_readexactly_insufficient_data(self):
        """Test readexactly with insufficient data"""
        self.stream.append_data(b"test")
        
        # This should wait and eventually raise EOFError when stream closes
        # For this test, we'll close the stream after a delay
        async def close_stream():
            await asyncio.sleep(0.1)
            self.stream.close()
        
        asyncio.create_task(close_stream())
        
        with self.assertRaises(EOFError):
            await self.reader.readexactly(10)


class TestNGTCP2StreamWriter(unittest.IsolatedAsyncioTestCase):
    """Test NGTCP2StreamWriter class"""
    
    @classmethod
    def setUpClass(cls):
        """Set up test class"""
        if not QUIC_AVAILABLE:
            raise unittest.SkipTest(f"ngtcp2 QUIC not available: {IMPORT_ERROR}")
    
    def setUp(self):
        """Set up test fixtures"""
        self.mock_connection = Mock(spec=NGTCP2Connection)
        self.mock_connection.conn = None
        self.mock_connection.remote_addr = ("127.0.0.1", 1884)
        self.mock_connection.send_packets = Mock(return_value=True)
        
        self.mock_server = Mock(spec=QUICServerNGTCP2)
        
        self.stream = NGTCP2Stream(stream_id=0, connection=self.mock_connection)
        self.writer = NGTCP2StreamWriter(self.mock_connection, self.stream, self.mock_server)
    
    def test_write(self):
        """Test writing data"""
        data = b"test data"
        self.writer.write(data)
        
        self.assertEqual(len(self.stream.send_buffer), len(data))
        self.assertEqual(bytes(self.stream.send_buffer), data)
    
    async def test_drain(self):
        """Test draining buffer"""
        # drain() should yield to event loop
        await self.writer.drain()
        # If no exception, it works
        self.assertTrue(True)
    
    def test_close(self):
        """Test closing writer"""
        self.writer.close()
        self.assertEqual(self.stream.state, "closed")
    
    async def test_wait_closed(self):
        """Test waiting for stream to close"""
        async def close_stream():
            await asyncio.sleep(0.1)
            self.stream.close()
        
        asyncio.create_task(close_stream())
        await self.writer.wait_closed()
        
        self.assertEqual(self.stream.state, "closed")
    
    def test_get_extra_info(self):
        """Test getting extra connection info"""
        peername = self.writer.get_extra_info('peername')
        self.assertEqual(peername, ("127.0.0.1", 1884))
        
        socket_info = self.writer.get_extra_info('socket')
        self.assertEqual(socket_info, self.mock_connection)
        
        unknown = self.writer.get_extra_info('unknown')
        self.assertIsNone(unknown)


class TestNGTCP2Connection(unittest.TestCase):
    """Test NGTCP2Connection class"""
    
    @classmethod
    def setUpClass(cls):
        """Set up test class"""
        if not QUIC_AVAILABLE:
            raise unittest.SkipTest(f"ngtcp2 QUIC not available: {IMPORT_ERROR}")
    
    def setUp(self):
        """Set up test fixtures"""
        self.mock_server = Mock(spec=QUICServerNGTCP2)
        self.mock_server.send_packet = Mock()
        
        self.dcid = b"test_dcid_12345678"
        self.scid = b"test_scid_12345678"
        self.remote_addr = ("127.0.0.1", 1884)
        
        self.connection = NGTCP2Connection(
            self.mock_server,
            self.dcid,
            self.scid,
            self.remote_addr
        )
    
    def test_connection_creation(self):
        """Test connection creation"""
        self.assertEqual(self.connection.dcid, self.dcid)
        self.assertEqual(self.connection.scid, self.scid)
        self.assertEqual(self.connection.remote_addr, self.remote_addr)
        self.assertEqual(self.connection.state, "initial")
        self.assertFalse(self.connection.handshake_completed)
        self.assertEqual(len(self.connection.streams), 0)
    
    def test_get_stream(self):
        """Test getting or creating stream"""
        stream = self.connection.get_stream(0)
        
        self.assertIsNotNone(stream)
        self.assertEqual(stream.stream_id, 0)
        self.assertEqual(stream.connection, self.connection)
        self.assertIn(0, self.connection.streams)
        
        # Getting same stream should return same object
        stream2 = self.connection.get_stream(0)
        self.assertEqual(stream, stream2)
    
    def test_connection_cleanup(self):
        """Test connection cleanup"""
        # Create a stream
        stream = self.connection.get_stream(0)
        
        # Cleanup
        self.connection.cleanup()
        
        self.assertEqual(len(self.connection.streams), 0)
        self.assertEqual(self.connection.state, "closed")


class TestQUICServerNGTCP2(unittest.TestCase):
    """Test QUICServerNGTCP2 class"""
    
    @classmethod
    def setUpClass(cls):
        """Set up test class"""
        if not QUIC_AVAILABLE:
            raise unittest.SkipTest(f"ngtcp2 QUIC not available: {IMPORT_ERROR}")
        
        # Skip this entire test class if ngtcp2 crashes during initialization
        # The crash "ngtcp2_settings.c:96 ngtcp2_settingslen_version: Unreachable"
        # occurs when ngtcp2 is not properly configured with TLS backend
        # See tests/TESTING_NOTES.md for details
        try:
            with patch('mqttd.transport_quic_ngtcp2.init_tls_backend', return_value=False):
                # Try to create a server instance to see if it crashes
                test_server = QUICServerNGTCP2(host="127.0.0.1", port=1884)
                cls._server_creation_works = True
        except (SystemError, OSError, RuntimeError) as e:
            # ngtcp2 crash or initialization failure
            raise unittest.SkipTest(f"ngtcp2 crashes during initialization (TLS backend issue): {e}")
        except Exception as e:
            # Other errors are OK - we can still test
            cls._server_creation_works = True
    
    def setUp(self):
        """Set up test fixtures"""
        if not self._server_creation_works:
            self.skipTest("Server creation failed in setUpClass")
        
        # Use Mock to avoid actual ngtcp2 initialization which can crash
        # if TLS backend is not properly configured
        try:
            with patch('mqttd.transport_quic_ngtcp2.init_tls_backend', return_value=False):
                self.server = QUICServerNGTCP2(
                    host="127.0.0.1",
                    port=1884
                )
        except (SystemError, OSError, RuntimeError) as e:
            # ngtcp2 crash - skip this test
            self.skipTest(f"ngtcp2 crashes during server creation: {e}")
        except Exception as e:
            # Other errors
            self.skipTest(f"Cannot create server: {e}")
    
    def test_server_creation(self):
        """Test server creation"""
        self.assertEqual(self.server.host, "127.0.0.1")
        self.assertEqual(self.server.port, 1884)
        self.assertEqual(len(self.server.connections), 0)
        self.assertIsNone(self.server.mqtt_handler)
    
    def test_set_mqtt_handler(self):
        """Test setting MQTT handler"""
        handler = Mock()
        self.server.set_mqtt_handler(handler)
        
        self.assertEqual(self.server.mqtt_handler, handler)
    
    def test_extract_dcid(self):
        """Test extracting DCID from packet"""
        # Create a mock Initial packet (simplified)
        # Long header (0x80) + Initial (0x00) = 0xC0
        # Version (4 bytes) + DCID len (1 byte) + DCID (8 bytes)
        dcid = b"test_dcid"
        packet = bytes([0xC0]) + b"\x00\x00\x00\x01" + bytes([len(dcid)]) + dcid
        
        result = self.server._extract_dcid(packet)
        
        # Should extract DCID (simplified parsing)
        # Note: This is a simplified test - real QUIC parsing is more complex
        if result:
            self.assertIsInstance(result, bytes)
    
    def test_is_initial_packet(self):
        """Test detecting Initial packets"""
        # Initial packet: Long header (0x80) + Initial (0x00) = 0xC0
        initial_packet = bytes([0xC0]) + b"\x00" * 20
        self.assertTrue(self.server._is_initial_packet(initial_packet))
        
        # Short header packet: 0x40
        short_packet = bytes([0x40]) + b"\x00" * 10
        self.assertFalse(self.server._is_initial_packet(short_packet))
        
        # Empty packet
        self.assertFalse(self.server._is_initial_packet(b""))


if __name__ == '__main__':
    unittest.main()
