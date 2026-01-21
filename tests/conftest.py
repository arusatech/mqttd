"""
Pytest configuration and fixtures for ngtcp2 tests
"""

import pytest
import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from mqttd.transport_quic_ngtcp2 import NGTCP2_AVAILABLE
except ImportError:
    NGTCP2_AVAILABLE = False


@pytest.fixture(scope="session")
def ngtcp2_available():
    """Fixture to check if ngtcp2 is available"""
    return NGTCP2_AVAILABLE


@pytest.fixture
def skip_if_no_ngtcp2(ngtcp2_available):
    """Fixture to skip tests if ngtcp2 is not available"""
    if not ngtcp2_available:
        pytest.skip("ngtcp2 not available")


@pytest.fixture
def mock_quic_server():
    """Fixture to create a mock QUIC server"""
    from unittest.mock import Mock
    from mqttd.transport_quic_ngtcp2 import QUICServerNGTCP2
    
    server = Mock(spec=QUICServerNGTCP2)
    server.host = "127.0.0.1"
    server.port = 1884
    server.connections = {}
    server.send_packet = Mock()
    return server


@pytest.fixture
def mock_connection(mock_quic_server):
    """Fixture to create a mock QUIC connection"""
    from mqttd.transport_quic_ngtcp2 import NGTCP2Connection
    
    connection = NGTCP2Connection(
        mock_quic_server,
        b"test_dcid_12345678",
        b"test_scid_12345678",
        ("127.0.0.1", 1884)
    )
    return connection


@pytest.fixture
def mock_stream(mock_connection):
    """Fixture to create a mock QUIC stream"""
    from mqttd.transport_quic_ngtcp2 import NGTCP2Stream
    
    stream = NGTCP2Stream(stream_id=0, connection=mock_connection)
    return stream
