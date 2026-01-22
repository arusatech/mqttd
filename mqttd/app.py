"""
MQTTD Application - FastAPI-like MQTT Server
"""

import asyncio
import ssl
import logging
import struct
from typing import Optional, Dict, List, Callable, Any, Tuple, Set
from pathlib import Path
import socket

from .protocol import (
    MQTTProtocol, MQTTMessageType, MQTTConnAckCode,
    MQTTConnectFlags
)
from .protocol_v5 import MQTT5Protocol
from .properties import PropertyEncoder, PropertyType
from .reason_codes import ReasonCode, MQTT3_TO_MQTT5_REASON_CODE
from .types import MQTTMessage, MQTTClient, QoS
from .decorators import subscribe, publish_handler, topic_matches
from .session import SessionManager, SessionState
from .thread_safe import ThreadSafeTopicTrie

logger = logging.getLogger(__name__)

# QUIC support - Priority: ngtcp2 > pure Python > aioquic
NGTCP2_AVAILABLE = False
try:
    # First try ngtcp2 (production-grade, best performance)
    from .transport_quic_ngtcp2 import QUICServerNGTCP2 as MQTTQuicServer, NGTCP2_AVAILABLE
    QUIC_AVAILABLE = NGTCP2_AVAILABLE
    if NGTCP2_AVAILABLE:
        logger.debug("QUIC: Using ngtcp2 (production-grade)")
except ImportError:
    NGTCP2_AVAILABLE = False
    try:
        # Fallback to pure Python (compatible with no-GIL)
        from .transport_quic_pure import QUICServer as MQTTQuicServer
        QUIC_AVAILABLE = True
        logger.debug("QUIC: Using pure Python implementation")
    except ImportError:
        try:
            # Fallback to aioquic (for regular Python, not no-GIL)
            from .transport_quic import MQTTQuicServer
            QUIC_AVAILABLE = True
            logger.debug("QUIC: Using aioquic")
        except ImportError:
            MQTTQuicServer = None
            QUIC_AVAILABLE = False
            logger.warning("QUIC not available. Using TCP only.")

try:
    import redis.asyncio as redis  # type: ignore[import-untyped]
except ImportError:
    redis = None  # type: ignore[assignment]
    logger.warning("Redis not available. Install with: pip install redis>=5.0.0")


class MQTTApp:
    """
    FastAPI-like MQTT/MQTTS server application.
    
    Usage:
        app = MQTTApp()
        
        @app.subscribe("sensors/temperature")
        async def handle_temp(message: MQTTMessage, client: MQTTClient):
            print(f"Temperature: {message.payload_str}")
        
        app.run(port=1883)
    """
    
    def __init__(self, 
                 host: str = "0.0.0.0",
                 port: int = 1883,
                 ssl_context: Optional[ssl.SSLContext] = None,
                 config_file: Optional[str] = None,
                 redis_host: Optional[str] = None,
                 redis_port: int = 6379,
                 redis_db: int = 0,
                 redis_password: Optional[str] = None,
                 redis_url: Optional[str] = None,
                 use_redis: bool = False,
                 enable_tcp: bool = True,
                 enable_quic: bool = False,
                 quic_port: int = 1884,
                 quic_certfile: Optional[str] = None,
                 quic_keyfile: Optional[str] = None,
                 max_connections: Optional[int] = None,
                 max_connections_per_ip: Optional[int] = None,
                 max_messages_per_second: Optional[float] = None,
                 max_subscriptions_per_minute: Optional[int] = None):
        """
        Initialize MQTT application.
        
        Args:
            host: Host to bind to (default: 0.0.0.0 for all interfaces)
            port: Port to listen on (default: 1883 for MQTT, 8883 for MQTTS)
            ssl_context: SSL context for MQTTS (TLS) support
            config_file: Path to configuration file (similar to C reference)
            redis_host: Redis server host (None = no Redis, use direct routing)
            redis_port: Redis server port (default: 6379)
            redis_db: Redis database number (default: 0)
            redis_password: Redis password (optional)
            redis_url: Redis connection URL (overrides host/port/db/password if provided)
            use_redis: Enable Redis pub/sub backend (default: False = direct routing)
            enable_tcp: Enable TCP transport (default: True). Set to False for QUIC-only mode.
            enable_quic: Enable QUIC/HTTP3 transport (default: False)
            quic_port: UDP port for QUIC server (default: 1884)
            quic_certfile: Path to TLS certificate for QUIC (required if enable_quic=True)
            quic_keyfile: Path to TLS private key for QUIC (required if enable_quic=True)
            max_connections: Maximum total connections (None = unlimited)
            max_connections_per_ip: Maximum connections per IP address (None = unlimited)
            max_messages_per_second: Rate limit for messages per second per client (None = unlimited)
            max_subscriptions_per_minute: Rate limit for subscriptions per minute per client (None = unlimited)
        """
        self.host = host
        self.port = port
        self.ssl_context = ssl_context
        self.config_file = config_file
        
        # Redis configuration (optional)
        self.use_redis = use_redis or (redis_host is not None) or (redis_url is not None)
        self.redis_host = redis_host or "localhost"
        self.redis_port = redis_port
        self.redis_db = redis_db
        self.redis_password = redis_password
        self.redis_url = redis_url
        
        # Transport configuration
        self.enable_tcp = enable_tcp
        self.enable_quic = enable_quic
        
        # Validate at least one transport is enabled
        if not self.enable_tcp and not self.enable_quic:
            raise ValueError("At least one transport must be enabled (enable_tcp or enable_quic)")
        
        # QUIC configuration (optional)
        self.quic_port = quic_port
        self.quic_certfile = quic_certfile
        self.quic_keyfile = quic_keyfile
        self._quic_server: Optional[MQTTQuicServer] = None
        
        # Route handlers
        self._subscribe_handlers: List[Dict[str, Any]] = []
        self._publish_handlers: List[Dict[str, Any]] = []
        
        # Client connections: socket -> (MQTTClient, writer) [legacy, kept for compatibility]
        self._clients: Dict[socket.socket, Tuple[MQTTClient, asyncio.StreamWriter]] = {}
        
        # Session management (MQTT 5.0 proper session handling)
        self._session_manager = SessionManager()
        
        # Topic subscriptions: topic -> Set of (socket, writer) tuples (legacy, kept for compatibility)
        self._topic_subscriptions: Dict[str, Set[Tuple[socket.socket, asyncio.StreamWriter]]] = {}
        
        # Trie-based topic matching for O(m) lookup performance (m = topic depth)
        self._topic_trie = ThreadSafeTopicTrie()
        
        # Retained messages: topic -> (payload, qos, retain)
        self._retained_messages: Dict[str, Tuple[bytes, int, bool]] = {}
        
        # Keepalive tracking: socket -> (last_activity, keepalive_seconds, ping_task)
        self._client_keepalive: Dict[socket.socket, Tuple[float, int, Optional[asyncio.Task]]] = {}
        
        # Connection limits and quotas
        self._max_connections = max_connections  # None = unlimited
        self._max_connections_per_ip = max_connections_per_ip  # None = unlimited
        self._connection_count = 0
        self._connections_per_ip: Dict[str, int] = {}  # IP -> connection count
        
        # Rate limiting: socket -> (message_count, window_start, subscription_count)
        self._rate_limits: Dict[socket.socket, Tuple[int, float, int]] = {}
        self._max_messages_per_second = max_messages_per_second  # None = unlimited
        self._max_subscriptions_per_minute = max_subscriptions_per_minute  # None = unlimited
        
        # Metrics
        self._metrics = {
            'total_connections': 0,
            'current_connections': 0,
            'total_messages_published': 0,
            'total_messages_received': 0,
            'total_subscriptions': 0,
            'total_unsubscriptions': 0,
        }
        
        # Flow control: socket -> (in_flight_qos_messages, receive_maximum)
        self._flow_control: Dict[socket.socket, Tuple[int, Optional[int]]] = {}
        
        # Shutdown flag for graceful shutdown
        self._shutdown_event: Optional[asyncio.Event] = None
        
        # Message batching: socket -> list of (topic, payload, qos) tuples
        self._message_batch: Dict[socket.socket, List[Tuple[str, bytes, int]]] = {}
        self._batch_size = 10  # Batch up to 10 messages
        self._batch_timeout = 0.01  # 10ms batching window
        
        # Redis connections
        self._redis_client: Optional[redis.Redis] = None
        self._redis_pubsub: Optional[redis.client.PubSub] = None
        self._redis_listener_task: Optional[asyncio.Task] = None
        
        # Configuration (similar to C reference)
        self._config = {
            'version': 5,  # MQTT 5.0
            'publish_before_suback': False,
            'short_publish': False,
            'excessive_remaining': False,
            'error_connack': 0,
            'testnum': 0
        }
        
        # Check QUIC availability (pure Python implementation is always available)
        if self.enable_quic and not QUIC_AVAILABLE:
            logger.warning(
                "QUIC requested but not available. "
                "Pure Python QUIC implementation should be available by default."
            )
            # Don't disable - pure Python implementation is included
        
        # Server state
        self._server: Optional[asyncio.Server] = None
        self._running = False
        
        # Load configuration
        if config_file:
            self._load_config()
    
    def _load_config(self):
        """Load configuration from file (similar to C reference)"""
        try:
            with open(self.config_file, 'r') as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith('#'):
                        continue
                    
                    parts = line.split(None, 1)
                    if len(parts) < 1:
                        continue
                    
                    key = parts[0]
                    value = parts[1] if len(parts) > 1 else None
                    
                    if key == "version" and value:
                        self._config['version'] = int(value)
                    elif key == "PUBLISH-before-SUBACK":
                        self._config['publish_before_suback'] = True
                    elif key == "short-PUBLISH":
                        self._config['short_publish'] = True
                    elif key == "error-CONNACK" and value:
                        self._config['error_connack'] = int(value)
                    elif key == "excessive-remaining":
                        self._config['excessive_remaining'] = True
                    elif key == "Testnum" and value:
                        self._config['testnum'] = int(value)
            
            logger.info(f"Loaded configuration from {self.config_file}")
        except FileNotFoundError:
            logger.warning(f"Config file {self.config_file} not found, using defaults")
        except Exception as e:
            logger.error(f"Error loading config: {e}")
    
    def subscribe(self, topic: str, qos: int = 0):
        """
        Decorator to subscribe to an MQTT topic.
        
        Usage:
            @app.subscribe("sensors/temperature")
            async def handle_temp(topic: str, client: MQTTClient):
                print(f"Subscribed to: {topic}")
        """
        def decorator(func: Callable) -> Callable:
            self._subscribe_handlers.append({
                'topic': topic,
                'qos': qos,
                'handler': func
            })
            logger.info(f"Registered subscribe handler: {topic}")
            return func
        return decorator
    
    def publish_handler(self, topic: Optional[str] = None):
        """
        Decorator to handle incoming PUBLISH messages.
        
        Usage:
            @app.publish_handler()
            async def handle_all(message: MQTTMessage, client: MQTTClient):
                print(f"Received: {message.topic}")
        """
        def decorator(func: Callable) -> Callable:
            self._publish_handlers.append({
                'topic': topic,
                'handler': func
            })
            logger.info(f"Registered publish handler: {topic or 'all'}")
            return func
        return decorator
    
    async def _read_fixed_header(self, reader: asyncio.StreamReader) -> Tuple[int, int, int]:
        """
        Read MQTT fixed header (similar to C reference fixedheader function).
        
        Returns:
            Tuple of (message_type, remaining_length, total_bytes_read)
        """
        # Read first two bytes
        header_data = await reader.readexactly(2)
        msg_type = header_data[0]
        
        # Decode remaining length (may be multi-byte)
        remaining_length, length_bytes = MQTTProtocol.decode_remaining_length(header_data, 1)
        
        # If we need more bytes for remaining length, read them
        if length_bytes > 1:
            extra_bytes = await reader.readexactly(length_bytes - 1)
            # Re-decode with full data
            full_header = header_data + extra_bytes
            remaining_length, _ = MQTTProtocol.decode_remaining_length(full_header, 1)
            total_bytes = 1 + length_bytes
        else:
            total_bytes = 2
        
        return msg_type, remaining_length, total_bytes
    
    async def _handle_connect(self, reader: asyncio.StreamReader,
                             writer: asyncio.StreamWriter,
                             client_sock: socket.socket) -> Optional[MQTTClient]:
        """Handle MQTT CONNECT message"""
        try:
            # Read fixed header
            msg_type, remaining_length, _ = await self._read_fixed_header(reader)
            
            if msg_type != MQTTMessageType.CONNECT:
                logger.warning(f"Expected CONNECT, got {hex(msg_type)}")
                return None
            
            # Read variable header and payload
            if remaining_length > 0:
                payload_data = await reader.readexactly(remaining_length)
            else:
                payload_data = b''
            
            # Parse CONNECT
            connect_info = MQTTProtocol.parse_connect(payload_data)
            
            # Verify protocol name
            if connect_info['protocol_name'] != 'MQTT':
                logger.warning("Invalid protocol name")
                return None
            
            # Detect protocol version (3.1.1 or 5.0)
            protocol_level = connect_info['protocol_level']
            is_mqtt5 = (protocol_level == MQTTProtocol.PROTOCOL_LEVEL_5_0)
            
            if not is_mqtt5 and protocol_level != MQTTProtocol.PROTOCOL_LEVEL_3_1_1:
                logger.warning(f"Unsupported protocol level: {protocol_level}")
                return None
            
            # Extract session parameters (now properly parsed from CONNECT message)
            clean_start = connect_info.get('clean_start', connect_info.get('clean_session', True))
            session_expiry_interval = connect_info.get('session_expiry_interval', 0)
            
            # MQTT 3.1.1 doesn't have session expiry
            if not is_mqtt5:
                session_expiry_interval = 0
            
            # Create client object
            client = MQTTClient(
                client_id=connect_info['client_id'],
                username=connect_info.get('username'),
                password=connect_info.get('password'),
                keepalive=connect_info['keepalive'],
                clean_session=clean_start,  # Use clean_start for consistency
                address=writer.get_extra_info('peername')
            )
            
            # Store protocol version for this client
            client_sock = writer.get_extra_info('socket')
            client._protocol_version = protocol_level  # Store for later use
            
            # Handle session management (per MQTT 5.0 spec for concurrent connections)
            session, old_session_to_disconnect = self._session_manager.create_session(
                client_id=connect_info['client_id'],
                socket_obj=client_sock,
                writer=writer,
                clean_start=clean_start,
                session_expiry_interval=session_expiry_interval
            )
            
            # Disconnect old connection if session takeover occurred
            if old_session_to_disconnect and old_session_to_disconnect.active_writer:
                try:
                    logger.info(f"Session takeover: Disconnecting old connection for ClientID {connect_info['client_id']}")
                    # Send DISCONNECT with Reason Code 0x8E (Session Taken Over) for MQTT 5.0
                    if old_session_to_disconnect.active_socket:
                        old_sock = old_session_to_disconnect.active_socket
                        if old_sock in self._clients:
                            _, old_writer = self._clients[old_sock]
                            if is_mqtt5:
                                disconnect_msg = MQTT5Protocol.build_disconnect_v5(
                                    reason_code=ReasonCode.SESSION_TAKEN_OVER
                                )
                            else:
                                disconnect_msg = MQTTProtocol.build_disconnect()
                            old_writer.write(disconnect_msg)
                            await old_writer.drain()
                            # Close old connection
                            old_writer.close()
                            await old_writer.wait_closed()
                            # Clean up old connection
                            await self._unsubscribe_client(old_sock)
                            if old_sock in self._clients:
                                del self._clients[old_sock]
                except Exception as e:
                    logger.error(f"Error disconnecting old connection: {e}")
            
            # Check connection limits
            client_ip = None
            if client.address:
                client_ip = client.address[0] if isinstance(client.address, tuple) else str(client.address)
            
            # Check total connection limit
            if self._max_connections is not None and self._connection_count >= self._max_connections:
                logger.warning(f"Connection limit reached ({self._max_connections}). Rejecting connection from {client_ip}")
                if is_mqtt5:
                    connack = MQTT5Protocol.build_connack_v5(reason_code=ReasonCode.SERVER_BUSY)
                else:
                    connack = MQTTProtocol.build_connack(MQTTConnAckCode.SERVER_UNAVAILABLE)
                writer.write(connack)
                await writer.drain()
                writer.close()
                await writer.wait_closed()
                return None
            
            # Check per-IP connection limit
            if client_ip and self._max_connections_per_ip is not None:
                ip_connection_count = self._connections_per_ip.get(client_ip, 0)
                if ip_connection_count >= self._max_connections_per_ip:
                    logger.warning(f"Per-IP connection limit reached ({self._max_connections_per_ip}) for {client_ip}")
                    if is_mqtt5:
                        connack = MQTT5Protocol.build_connack_v5(reason_code=ReasonCode.CONNECTION_RATE_EXCEEDED)
                    else:
                        connack = MQTTProtocol.build_connack(MQTTConnAckCode.SERVER_UNAVAILABLE)
                    writer.write(connack)
                    await writer.drain()
                    writer.close()
                    await writer.wait_closed()
                    return None
                
                # Increment IP connection count
                self._connections_per_ip[client_ip] = ip_connection_count + 1
            
            # Increment total connection count
            self._connection_count += 1
            self._metrics['total_connections'] += 1
            self._metrics['current_connections'] = self._connection_count
            
            logger.info(f"Client connected: {client} (MQTT {'5.0' if is_mqtt5 else '3.1.1'}, Clean Start: {clean_start})")
            
            # Determine session_present for CONNACK
            session_present = False
            if not clean_start and session and len(session.subscriptions) > 0:
                session_present = True
            
            # Extract receive_maximum from CONNECT properties (MQTT 5.0)
            receive_maximum = None
            if is_mqtt5:
                connect_properties = connect_info.get('properties', {})
                receive_maximum = connect_properties.get(PropertyType.RECEIVE_MAXIMUM)
                # Default to 65535 if not specified (MQTT 5.0 spec)
                if receive_maximum is None:
                    receive_maximum = 65535
            
            # Initialize flow control tracking
            if receive_maximum is not None:
                self._flow_control[client_sock] = (0, receive_maximum)
            
            # Send CONNACK based on protocol version
            if is_mqtt5:
                # MQTT 5.0 CONNACK with reason code
                connack_reason_code = ReasonCode.SUCCESS
                if self._config.get('error_connack'):
                    # Map MQTT 3.1.1 return code to MQTT 5.0 reason code
                    mqtt3_code = self._config.get('error_connack')
                    connack_reason_code = MQTT3_TO_MQTT5_REASON_CODE.get(
                        mqtt3_code, ReasonCode.UNSPECIFIED_ERROR
                    )
                
                # Server's receive maximum (how many QoS > 0 messages we can receive from client)
                server_receive_maximum = 65535  # Default
                
                connack = MQTT5Protocol.build_connack_v5(
                    reason_code=connack_reason_code,
                    session_present=session_present,
                    retain_available=1,
                    maximum_qos=2,
                    wildcard_subscription_available=1,
                    subscription_identifier_available=1,
                    shared_subscription_available=0,  # Not implemented yet
                    receive_maximum=server_receive_maximum  # Tell client our receive maximum
                )
            else:
                # MQTT 3.1.1 CONNACK
                connack_code = self._config.get('error_connack', MQTTConnAckCode.ACCEPTED)
                connack = MQTTProtocol.build_connack(connack_code)
            
            writer.write(connack)
            await writer.drain()
            
            # Store client connection (legacy dict for compatibility)
            client_sock = writer.get_extra_info('socket')
            self._clients[client_sock] = (client, writer)
            
            # Track keepalive for this client
            keepalive_seconds = client.keepalive if client.keepalive > 0 else 60
            self._client_keepalive[client_sock] = (asyncio.get_event_loop().time(), keepalive_seconds, None)
            
            # Start keepalive monitoring task
            if keepalive_seconds > 0:
                ping_task = asyncio.create_task(self._keepalive_monitor(client_sock, keepalive_seconds))
                self._client_keepalive[client_sock] = (asyncio.get_event_loop().time(), keepalive_seconds, ping_task)
            
            # Link session to client
            client._session = session
            
            return client
            
        except Exception as e:
            logger.error(f"Error handling CONNECT: {e}")
            return None
    
    async def _connect_redis(self):
        """Connect to Redis"""
        if redis is None:
            raise RuntimeError("Redis not available. Install with: pip install redis>=5.0.0")
        
        try:
            if self.redis_url:
                self._redis_client = redis.from_url(self.redis_url, decode_responses=False)
            else:
                self._redis_client = redis.Redis(
                    host=self.redis_host,
                    port=self.redis_port,
                    db=self.redis_db,
                    password=self.redis_password,
                    decode_responses=False  # Keep binary for MQTT payloads
                )
            
            # Test connection
            await self._redis_client.ping()
            logger.info(f"Connected to Redis at {self.redis_host}:{self.redis_port}")
            
            # Create pubsub client
            self._redis_pubsub = self._redis_client.pubsub()
            
            # Start Redis message listener
            self._redis_listener_task = asyncio.create_task(self._redis_message_listener())
            
        except Exception as e:
            logger.error(f"Failed to connect to Redis: {e}")
            raise
    
    async def _disconnect_redis(self):
        """Disconnect from Redis"""
        if self._redis_listener_task:
            self._redis_listener_task.cancel()
            try:
                await self._redis_listener_task
            except asyncio.CancelledError:
                pass
        
        if self._redis_pubsub:
            await self._redis_pubsub.unsubscribe()
            await self._redis_pubsub.close()
            self._redis_pubsub = None
        
        if self._redis_client:
            await self._redis_client.close()
            self._redis_client = None
        
        logger.info("Disconnected from Redis")
    
    async def _redis_message_listener(self):
        """Listen for messages from Redis and forward to MQTT clients"""
        try:
            while self._running:
                try:
                    # Get message with timeout to allow checking self._running
                    message = await asyncio.wait_for(
                        self._redis_pubsub.get_message(ignore_subscribe_messages=True),
                        timeout=1.0
                    )
                    
                    if message and message['type'] == 'message':
                        channel = message['channel'].decode('utf-8') if isinstance(message['channel'], bytes) else message['channel']
                        payload = message['data']
                        
                        # Forward to all subscribed MQTT clients
                        await self._forward_to_mqtt_clients(channel, payload)
                        
                except asyncio.TimeoutError:
                    continue
                except Exception as e:
                    logger.error(f"Error in Redis message listener: {e}")
                    await asyncio.sleep(0.1)
                    
        except asyncio.CancelledError:
            logger.info("Redis message listener cancelled")
        except Exception as e:
            logger.error(f"Redis message listener error: {e}")
    
    async def _route_directly(self, topic: str, payload: bytes, qos: int = 0,
                              subscription_identifiers: Optional[List[int]] = None,
                              message_expiry_time: Optional[float] = None):
        """
        Route message directly to subscribed clients (no Redis).
        Lower latency for single-server deployments.
        """
        await self._forward_to_mqtt_clients(topic, payload, qos, subscription_identifiers, message_expiry_time)
    
    async def _forward_to_mqtt_clients(self, topic: str, payload: bytes, qos: int = 0, 
                                       subscription_identifiers: Optional[List[int]] = None,
                                       message_expiry_time: Optional[float] = None):
        """
        Forward message to subscribed MQTT clients (used by both Redis and direct routing).
        
        Args:
            topic: Topic name
            payload: Message payload
            qos: QoS level
            subscription_identifiers: List of subscription IDs for MQTT 5.0
            message_expiry_time: Absolute time when message expires (None = no expiry)
        """
        # Check message expiry before forwarding
        if message_expiry_time is not None:
            current_time = asyncio.get_event_loop().time()
            if current_time >= message_expiry_time:
                logger.debug(f"Message expired, not forwarding: {topic}")
                return
        
        # Use Trie-based lookup for O(m) performance where m = topic depth
        # This is much faster than O(n) where n = number of subscriptions
        clients_to_notify = self._topic_trie.find_matching(topic)
        
        if not clients_to_notify:
            logger.debug(f"No subscribers for topic: {topic}")
            return
        
        # Send to all subscribed clients (build message per client for MQTT 5.0 subscription IDs)
        disconnected_clients = []
        for client_sock, writer in clients_to_notify:
            try:
                # Get client object to check protocol version
                client_obj = None
                if client_sock in self._clients:
                    client_obj, _ = self._clients[client_sock]
                
                # Build PUBLISH message (MQTT 5.0 with subscription IDs if applicable)
                is_mqtt5 = client_obj and hasattr(client_obj, '_protocol_version') and client_obj._protocol_version == MQTTProtocol.PROTOCOL_LEVEL_5_0
                
                if is_mqtt5 and subscription_identifiers:
                    # Find subscription IDs for this specific subscription
                    sub_ids_for_client = []
                    if client_obj and client_obj._session:
                        # Get subscription ID from session for this topic
                        subscription = client_obj._session.subscriptions.get(topic)
                        if subscription and subscription.subscription_id:
                            sub_ids_for_client = [subscription.subscription_id]
                    
                    # Build MQTT 5.0 PUBLISH with subscription identifiers
                    publish_msg = MQTT5Protocol.build_publish_v5(
                        topic=topic,
                        payload=payload,
                        packet_id=None,
                        qos=qos,
                        retain=False,
                        subscription_identifiers=sub_ids_for_client if sub_ids_for_client else None
                    )
                else:
                    # MQTT 3.1.1 or no subscription IDs
                    publish_msg = MQTTProtocol.build_publish(topic, payload, None, qos)
                
                writer.write(publish_msg)
                await writer.drain()
                logger.debug(f"Forwarded message to client on topic: {topic}")
            except Exception as e:
                logger.debug(f"Failed to send to client {client_sock}: {e}")
                disconnected_clients.append(client_sock)
        
        # Clean up disconnected clients
        for client_sock in disconnected_clients:
            await self._unsubscribe_client(client_sock)
    
    async def _handle_subscribe(self, reader: asyncio.StreamReader,
                               writer: asyncio.StreamWriter,
                               client: MQTTClient,
                               remaining_length: int) -> bool:
        """Handle MQTT SUBSCRIBE message - subscribe to Redis channel"""
        try:
            # Read variable header and payload
            data = await reader.readexactly(remaining_length)
            
            # Check if MQTT 5.0 client
            is_mqtt5 = hasattr(client, '_protocol_version') and client._protocol_version == MQTTProtocol.PROTOCOL_LEVEL_5_0
            
            # Parse SUBSCRIBE (MQTT 5.0 with properties or MQTT 3.1.1)
            if is_mqtt5:
                subscribe_info = MQTT5Protocol.parse_subscribe_v5(data)
            else:
                subscribe_info = MQTTProtocol.parse_subscribe(data)
            
            topic = subscribe_info['topic']
            packet_id = subscribe_info['packet_id']
            qos = subscribe_info['qos']
            subscription_identifier = subscribe_info.get('subscription_identifier') if is_mqtt5 else None
            
            logger.info(f"SUBSCRIBE: topic={topic}, packet_id={packet_id}, qos={qos}, sub_id={subscription_identifier}")
            
            client_sock = writer.get_extra_info('socket')
            
            # Check subscription rate limiting
            if not self._check_rate_limit(client_sock, check_subscription=True):
                # Rate limited - send error SUBACK
                logger.warning(f"Subscription rate limited for client {client_sock}")
                is_mqtt5 = hasattr(client, '_protocol_version') and client._protocol_version == MQTTProtocol.PROTOCOL_LEVEL_5_0
                if is_mqtt5:
                    suback = MQTT5Protocol.build_suback_v5(
                        packet_id, 
                        [ReasonCode.QUOTA_EXCEEDED]
                    )
                else:
                    suback = MQTTProtocol.build_suback(packet_id, 0x80)  # Error return code
                writer.write(suback)
                await writer.drain()
                return False
            
            # Subscribe to Redis channel (if using Redis)
            if self.use_redis and self._redis_pubsub:
                await self._redis_pubsub.subscribe(topic)
                logger.info(f"Subscribed to Redis channel: {topic}")
            
            # Track MQTT client subscription (for both Redis and direct routing)
            # Maintain legacy dict for backward compatibility
            if topic not in self._topic_subscriptions:
                self._topic_subscriptions[topic] = set()
            self._topic_subscriptions[topic].add((client_sock, writer))
            
            # Add to Trie for O(m) lookup performance
            self._topic_trie.insert(topic, (client_sock, writer))
            
            # Store subscription identifier in session (MQTT 5.0)
            if is_mqtt5 and client._session and subscription_identifier:
                client._session.add_subscription(topic, qos, subscription_identifier)
            
            # Find matching handlers
            matching_handlers = []
            for handler in self._subscribe_handlers:
                if topic_matches(handler['topic'], topic):
                    matching_handlers.append(handler)
            
            # Execute handlers (they can return payload to send)
            payload = None
            for handler_info in matching_handlers:
                handler = handler_info['handler']
                try:
                    if asyncio.iscoroutinefunction(handler):
                        result = await handler(topic, client)
                    else:
                        result = handler(topic, client)
                    
                    # Use first handler's result as payload
                    if payload is None and result is not None:
                        if isinstance(result, bytes):
                            payload = result
                        elif isinstance(result, str):
                            payload = result.encode('utf-8')
                except Exception as e:
                    logger.debug(f"Handler error (non-fatal): {e}")
            
            # Send SUBACK (MQTT 5.0 with reason codes if applicable)
            if not self._config.get('publish_before_suback', False):
                if is_mqtt5:
                    suback = MQTT5Protocol.build_suback_v5(packet_id, [ReasonCode.GRANTED_QOS_0 + min(qos, 2)])
                else:
                    suback = MQTTProtocol.build_suback(packet_id, 0)
                writer.write(suback)
                await writer.drain()
            
            # Send PUBLISH if we have handlers (default payload if none returned)
            if matching_handlers and payload:
                publish_msg = MQTTProtocol.build_publish(topic, payload, packet_id, qos)
                
                if self._config.get('short_publish', False):
                    # Truncate for testing
                    publish_msg = publish_msg[:-2]
                
                writer.write(publish_msg)
                await writer.drain()
                logger.info(f"Published to {topic}")
            
            # Send SUBACK if not sent before
            if self._config.get('publish_before_suback', False):
                if is_mqtt5:
                    suback = MQTT5Protocol.build_suback_v5(packet_id, [ReasonCode.GRANTED_QOS_0 + min(qos, 2)])
                else:
                    suback = MQTTProtocol.build_suback(packet_id, 0)
                writer.write(suback)
                await writer.drain()
            
            # Deliver retained messages matching this subscription
            await self._deliver_retained_messages(topic, client_sock, writer, qos)
            
            # Update metrics
            self._metrics['total_subscriptions'] += 1
            
            return True
            
        except Exception as e:
            logger.error(f"Error handling SUBSCRIBE: {e}")
            return False
    
    async def _unsubscribe_client(self, client_sock: socket.socket):
        """Unsubscribe client from all topics"""
        topics_to_remove = []
        client_writer = None
        
        # Find all topics this client is subscribed to
        for topic, subscriptions in self._topic_subscriptions.items():
            subscriptions_to_remove = []
            for sock, writer in subscriptions:
                if sock == client_sock:
                    subscriptions_to_remove.append((sock, writer))
                    if client_writer is None:
                        client_writer = writer
            
            for item in subscriptions_to_remove:
                subscriptions.discard(item)
                # Also remove from Trie
                if client_writer:
                    self._topic_trie.remove(topic, item)
            
            if not subscriptions:
                topics_to_remove.append(topic)
        
        # Unsubscribe from Redis if no more clients (only if using Redis)
        if self.use_redis and self._redis_pubsub:
            for topic in topics_to_remove:
                await self._redis_pubsub.unsubscribe(topic)
                logger.info(f"Unsubscribed from Redis channel: {topic}")
        
        # Remove empty topic entries
        for topic in topics_to_remove:
            del self._topic_subscriptions[topic]
    
    def _check_rate_limit(self, client_sock: socket.socket, check_subscription: bool = False) -> bool:
        """
        Check rate limits for client.
        
        Args:
            client_sock: Client socket
            check_subscription: If True, check subscription rate limit; otherwise check message rate limit
        
        Returns:
            True if within limits, False if rate limited
        """
        if client_sock not in self._rate_limits:
            # Initialize rate limit tracking
            current_time = asyncio.get_event_loop().time()
            self._rate_limits[client_sock] = (0, current_time, 0)  # (messages, window_start, subscriptions)
        
        msg_count, window_start, sub_count = self._rate_limits[client_sock]
        current_time = asyncio.get_event_loop().time()
        
        # Check message rate limit
        if not check_subscription and self._max_messages_per_second is not None:
            # Reset window if more than 1 second has passed
            if current_time - window_start >= 1.0:
                msg_count = 0
                window_start = current_time
            
            if msg_count >= self._max_messages_per_second:
                logger.warning(f"Rate limit exceeded for client {client_sock}: {msg_count} messages/second")
                return False
            
            # Increment message count
            msg_count += 1
        
        # Check subscription rate limit
        if check_subscription and self._max_subscriptions_per_minute is not None:
            # Reset window if more than 1 minute has passed
            if current_time - window_start >= 60.0:
                sub_count = 0
                window_start = current_time
            
            if sub_count >= self._max_subscriptions_per_minute:
                logger.warning(f"Subscription rate limit exceeded for client {client_sock}: {sub_count} subscriptions/minute")
                return False
            
            # Increment subscription count
            sub_count += 1
        
        # Update rate limit tracking
        self._rate_limits[client_sock] = (msg_count, window_start, sub_count)
        return True
    
    async def _handle_publish(self, reader: asyncio.StreamReader,
                             writer: asyncio.StreamWriter,
                             client: MQTTClient,
                             msg_type: int,
                             remaining_length: int) -> bool:
        """Handle MQTT PUBLISH message - publish to Redis channel"""
        try:
            client_sock = writer.get_extra_info('socket')
            
            # Check rate limiting
            if not self._check_rate_limit(client_sock, check_subscription=False):
                # Rate limited - could send error response, but for now just log and skip
                logger.warning(f"Rate limited PUBLISH from client {client_sock}")
                return False
            
            # Read variable header and payload
            data = await reader.readexactly(remaining_length)
            
            # Extract QoS from message type
            qos = (msg_type >> 1) & 0x03
            
            # Check if MQTT 5.0 client
            is_mqtt5 = hasattr(client, '_protocol_version') and client._protocol_version == MQTTProtocol.PROTOCOL_LEVEL_5_0
            
            # Parse PUBLISH (MQTT 5.0 with properties support or MQTT 3.1.1)
            if is_mqtt5:
                publish_info = MQTT5Protocol.parse_publish_v5(data, qos)
                
                # Handle topic alias (MQTT 5.0)
                topic = publish_info['topic']
                topic_alias = publish_info.get('topic_alias')
                
                if topic_alias is not None:
                    # Resolve topic from alias or store new alias
                    if client._session:
                        if topic is None or topic == '':
                            # Using alias - resolve from session
                            topic = client._session.topic_aliases.get(topic_alias)
                            if topic is None:
                                logger.warning(f"Unknown topic alias {topic_alias} from client {client.client_id}")
                                return False
                        else:
                            # Setting new alias - store in session
                            if topic_alias > 0:
                                client._session.topic_aliases[topic_alias] = topic
                                logger.debug(f"Stored topic alias {topic_alias} -> {topic} for client {client.client_id}")
                
                if topic is None:
                    logger.error("PUBLISH missing topic and no valid alias")
                    return False
                
                # Check message expiry (MQTT 5.0)
                message_expiry_interval = publish_info.get('message_expiry_interval')
                if message_expiry_interval is not None and message_expiry_interval > 0:
                    # Store expiry time for later checking
                    expiry_time = asyncio.get_event_loop().time() + message_expiry_interval
                    # We'll check this before delivering
                
                # Check flow control (Receive Maximum) for QoS > 0
                if qos > 0:
                    if client_sock not in self._flow_control:
                        # Initialize flow control (default receive_maximum from CONNACK)
                        self._flow_control[client_sock] = (0, None)
                    
                    in_flight, receive_max = self._flow_control[client_sock]
                    if receive_max is not None and in_flight >= receive_max:
                        logger.warning(f"Receive Maximum exceeded for client {client.client_id}: {in_flight}/{receive_max}")
                        # Could send DISCONNECT with RECEIVE_MAXIMUM_EXCEEDED, but continue for now
                
                subscription_identifiers = publish_info.get('subscription_identifiers', [])
            else:
                # MQTT 3.1.1 parsing
                publish_info = MQTTProtocol.parse_publish(data, qos)
                topic = publish_info['topic']
                subscription_identifiers = []
            
            # Create message object
            message = MQTTMessage(
                topic=topic,
                payload=publish_info['payload'],
                qos=qos,
                retain=bool(msg_type & 0x01),
                packet_id=publish_info.get('packet_id')
            )
            
            # Store subscription identifiers for forwarding
            message._subscription_identifiers = subscription_identifiers if is_mqtt5 else []
            
            logger.info(f"PUBLISH: topic={message.topic}, payload_len={len(message.payload)}")
            
            # Update metrics
            self._metrics['total_messages_received'] += 1
            
            # Handle retained messages
            if message.retain:
                if len(message.payload) == 0:
                    # Empty payload with retain flag = delete retained message
                    self._retained_messages.pop(message.topic, None)
                    logger.debug(f"Deleted retained message for topic: {message.topic}")
                else:
                    # Store retained message
                    self._retained_messages[message.topic] = (message.payload, qos, message.retain)
                    logger.debug(f"Stored retained message for topic: {message.topic}")
            
            # Route message: Redis pub/sub OR direct routing
            if self.use_redis and self._redis_client:
                # Publish to Redis channel (for distributed/multi-server)
                await self._redis_client.publish(message.topic, message.payload)
                logger.debug(f"Published to Redis channel: {message.topic}")
            else:
                # Direct routing: send directly to subscribed clients (lower latency)
                # Pass subscription identifiers and expiry time
                expiry_time = None
                if is_mqtt5 and message_expiry_interval is not None:
                    expiry_time = asyncio.get_event_loop().time() + message_expiry_interval
                
                await self._forward_to_mqtt_clients(
                    message.topic, 
                    message.payload, 
                    qos,
                    subscription_identifiers=getattr(message, '_subscription_identifiers', None),
                    message_expiry_time=expiry_time
                )
            
            # Find matching handlers
            matching_handlers = []
            for handler in self._publish_handlers:
                if handler['topic'] is None or topic_matches(handler['topic'], message.topic):
                    matching_handlers.append(handler)
            
            # Execute handlers
            for handler_info in matching_handlers:
                handler = handler_info['handler']
                try:
                    if asyncio.iscoroutinefunction(handler):
                        await handler(message, client)
                    else:
                        handler(message, client)
                except Exception as e:
                    logger.error(f"Error in publish handler: {e}")
            
            # Update keepalive activity
            client_sock = writer.get_extra_info('socket')
            if client_sock in self._client_keepalive:
                current_time = asyncio.get_event_loop().time()
                keepalive_seconds = self._client_keepalive[client_sock][1]
                self._client_keepalive[client_sock] = (current_time, keepalive_seconds, self._client_keepalive[client_sock][2])
            
            # Update flow control (increment in-flight for QoS > 0)
            if qos > 0 and client_sock in self._flow_control:
                in_flight, receive_max = self._flow_control[client_sock]
                self._flow_control[client_sock] = (in_flight + 1, receive_max)
            
            # Send response based on QoS level
            if qos == 1 and message.packet_id is not None:
                # QoS 1: Send PUBACK
                puback = MQTTProtocol.build_puback(message.packet_id)
                writer.write(puback)
                await writer.drain()
                # Decrement flow control when PUBACK sent
                if client_sock in self._flow_control:
                    in_flight, receive_max = self._flow_control[client_sock]
                    self._flow_control[client_sock] = (max(0, in_flight - 1), receive_max)
            elif qos == 2 and message.packet_id is not None:
                # QoS 2: Send PUBREC (will handle PUBREL/PUBCOMP later)
                pubrec = MQTTProtocol.build_pubrec(message.packet_id)
                writer.write(pubrec)
                await writer.drain()
                # Store in session for PUBREL handling
                if client._session:
                    client._session.pending_pubrec[message.packet_id] = message
                # Decrement flow control when PUBCOMP received (handled in _handle_pubrel)
            
            return True
            
        except Exception as e:
            logger.error(f"Error handling PUBLISH: {e}")
            return False
    
    async def _handle_client(self, reader: asyncio.StreamReader,
                            writer: asyncio.StreamWriter):
        """Handle a client connection"""
        client_sock = writer.get_extra_info('socket')
        client = None
        
        try:
            # Reset config for each connection (like C reference)
            if self.config_file:
                self._load_config()
            
            # Handle CONNECT
            client = await self._handle_connect(reader, writer, client_sock)
            if not client:
                return
            
            # Main message loop
            # Use keepalive timeout (with buffer) or default 60 seconds
            keepalive_timeout = 60.0
            if client_sock in self._client_keepalive:
                keepalive_seconds = self._client_keepalive[client_sock][1]
                # Timeout at 1.5x keepalive to allow for network delay
                keepalive_timeout = (keepalive_seconds * 1.5) if keepalive_seconds > 0 else 60.0
            
            while True:
                try:
                    # Read fixed header (with timeout based on keepalive)
                    msg_type, remaining_length, _ = await asyncio.wait_for(
                        self._read_fixed_header(reader),
                        timeout=keepalive_timeout
                    )
                    
                    # Update keepalive activity on any message received
                    if client_sock in self._client_keepalive:
                        current_time = asyncio.get_event_loop().time()
                        keepalive_seconds = self._client_keepalive[client_sock][1]
                        self._client_keepalive[client_sock] = (current_time, keepalive_seconds, self._client_keepalive[client_sock][2])
                    
                    # Validate message type (must be valid MQTT message type)
                    if msg_type == 0x00 or (msg_type & 0xF0) == 0xF0:
                        logger.warning(f"Invalid message type: {hex(msg_type)}")
                        break
                    
                    # Handle message based on type
                    if msg_type == MQTTMessageType.SUBSCRIBE:
                        await self._handle_subscribe(reader, writer, client, remaining_length)
                        # Continue loop for persistent connection
                    
                    elif (msg_type & 0xF0) == MQTTMessageType.PUBLISH:
                        await self._handle_publish(reader, writer, client, msg_type, remaining_length)
                        # Continue loop for persistent connection
                    
                    elif msg_type == MQTTMessageType.PINGREQ:
                        await self._handle_pingreq(writer)
                        # Continue loop for persistent connection
                    
                    elif msg_type == MQTTMessageType.UNSUBSCRIBE:
                        await self._handle_unsubscribe(reader, writer, client, remaining_length)
                        # Continue loop for persistent connection
                    
                    elif msg_type == MQTTMessageType.DISCONNECT:
                        logger.info("Received DISCONNECT")
                        break
                    
                    elif msg_type == MQTTMessageType.PUBACK:
                        # QoS 1 acknowledgment - just log for now
                        logger.debug("Received PUBACK")
                        # Continue loop
                    
                    elif msg_type == MQTTMessageType.PUBREC:
                        # QoS 2 flow - handle PUBREC
                        await self._handle_pubrec(reader, writer, client, remaining_length)
                        # Continue loop
                    
                    elif msg_type == MQTTMessageType.PUBREL:
                        # QoS 2 flow - handle PUBREL
                        await self._handle_pubrel(reader, writer, client, remaining_length)
                        # Continue loop
                    
                    elif msg_type == MQTTMessageType.PUBCOMP:
                        # QoS 2 flow - just log for now
                        logger.debug("Received PUBCOMP")
                        # Continue loop
                    
                    else:
                        logger.warning(f"Unsupported message type: {hex(msg_type)}")
                        break
                        
                except asyncio.TimeoutError:
                    logger.info("Client timeout")
                    break
                except asyncio.IncompleteReadError:
                    logger.info("Client disconnected")
                    break
                except Exception as e:
                    logger.error(f"Error in message loop: {e}")
                    break
        
        except Exception as e:
            logger.error(f"Error handling client: {e}")
        finally:
            # Unsubscribe client from all topics
            await self._unsubscribe_client(client_sock)
            
            # Remove client connection
            if client_sock in self._clients:
                del self._clients[client_sock]
            
            # Handle session cleanup (per MQTT 5.0 spec)
            preserved_session = self._session_manager.remove_connection(client_sock)
            if preserved_session:
                logger.info(f"Session preserved for ClientID {preserved_session.client_id} (expiry: {preserved_session.expiry_interval}s)")
            else:
                logger.debug(f"Session removed for disconnected client")
            
            # Clean up keepalive tracking
            if client_sock in self._client_keepalive:
                keepalive_info = self._client_keepalive.pop(client_sock)
                if keepalive_info[2]:  # Cancel ping task if exists
                    keepalive_info[2].cancel()
            
            # Clean up connection limits
            if client_sock in self._clients:
                client_obj, _ = self._clients[client_sock]
                if client_obj.address:
                    client_ip = client_obj.address[0] if isinstance(client_obj.address, tuple) else str(client_obj.address)
                    if client_ip in self._connections_per_ip:
                        self._connections_per_ip[client_ip] -= 1
                        if self._connections_per_ip[client_ip] <= 0:
                            del self._connections_per_ip[client_ip]
            
            self._connection_count = max(0, self._connection_count - 1)
            self._metrics['current_connections'] = self._connection_count
            
            # Clean up rate limiting
            self._rate_limits.pop(client_sock, None)
            
            # Clean up flow control
            self._flow_control.pop(client_sock, None)
            
            writer.close()
            await writer.wait_closed()
            logger.info("Client disconnected")
    
    async def _handle_pingreq(self, writer: asyncio.StreamWriter):
        """Handle PINGREQ message - send PINGRESP"""
        try:
            pingresp = MQTTProtocol.build_pingresp()
            writer.write(pingresp)
            await writer.drain()
            logger.debug("Sent PINGRESP")
            
            # Update keepalive activity
            client_sock = writer.get_extra_info('socket')
            if client_sock in self._client_keepalive:
                current_time = asyncio.get_event_loop().time()
                keepalive_seconds = self._client_keepalive[client_sock][1]
                self._client_keepalive[client_sock] = (current_time, keepalive_seconds, self._client_keepalive[client_sock][2])
        except Exception as e:
            logger.error(f"Error handling PINGREQ: {e}")
    
    async def _handle_unsubscribe(self, reader: asyncio.StreamReader,
                                  writer: asyncio.StreamWriter,
                                  client: MQTTClient,
                                  remaining_length: int) -> bool:
        """Handle UNSUBSCRIBE message"""
        try:
            # Read variable header and payload
            data = await reader.readexactly(remaining_length)
            
            # Parse UNSUBSCRIBE
            unsubscribe_info = MQTTProtocol.parse_unsubscribe(data)
            packet_id = unsubscribe_info['packet_id']
            topics = unsubscribe_info['topics']
            
            logger.info(f"UNSUBSCRIBE: packet_id={packet_id}, topics={topics}")
            
            client_sock = writer.get_extra_info('socket')
            
            # Remove subscriptions
            for topic in topics:
                # Remove from legacy topic subscriptions dict
                if topic in self._topic_subscriptions:
                    subscriptions_to_remove = []
                    for sock, wr in self._topic_subscriptions[topic]:
                        if sock == client_sock:
                            subscriptions_to_remove.append((sock, wr))
                    
                    for item in subscriptions_to_remove:
                        self._topic_subscriptions[topic].discard(item)
                    
                    # Remove empty topic entries
                    if not self._topic_subscriptions[topic]:
                        del self._topic_subscriptions[topic]
                        # Unsubscribe from Redis if using Redis
                        if self.use_redis and self._redis_pubsub:
                            await self._redis_pubsub.unsubscribe(topic)
                            logger.debug(f"Unsubscribed from Redis channel: {topic}")
                
                # Remove from Trie
                self._topic_trie.remove(topic, (client_sock, writer))
                
                # Remove from session if exists
                if client._session:
                    client._session.remove_subscription(topic)
            
            # Update metrics
            self._metrics['total_unsubscriptions'] += 1
            
            # Send UNSUBACK (MQTT 5.0 with reason codes if applicable)
            is_mqtt5 = hasattr(client, '_protocol_version') and client._protocol_version == MQTTProtocol.PROTOCOL_LEVEL_5_0
            
            if is_mqtt5:
                # MQTT 5.0: Send UNSUBACK with reason codes (one per topic)
                reason_codes = []
                for topic in topics:
                    # Check if subscription existed
                    if topic in self._topic_subscriptions:
                        # Check if client had this subscription
                        had_subscription = any(sock == client_sock for sock, _ in self._topic_subscriptions.get(topic, set()))
                        if had_subscription:
                            reason_codes.append(ReasonCode.SUCCESS_UNSUB)
                        else:
                            reason_codes.append(ReasonCode.NO_SUBSCRIPTION_EXISTED)
                    else:
                        reason_codes.append(ReasonCode.NO_SUBSCRIPTION_EXISTED)
                
                unsuback = MQTT5Protocol.build_unsuback_v5(packet_id, reason_codes)
            else:
                # MQTT 3.1.1: Simple UNSUBACK
                unsuback = MQTTProtocol.build_unsuback(packet_id)
            
            writer.write(unsuback)
            await writer.drain()
            
            return True
            
        except Exception as e:
            logger.error(f"Error handling UNSUBSCRIBE: {e}")
            return False
    
    async def _handle_pubrec(self, reader: asyncio.StreamReader,
                            writer: asyncio.StreamWriter,
                            client: MQTTClient,
                            remaining_length: int):
        """Handle PUBREC message (QoS 2 flow) - respond with PUBREL"""
        try:
            if remaining_length >= 2:
                data = await reader.readexactly(remaining_length)
                packet_id = struct.unpack('>H', data[:2])[0]
                
                # Send PUBREL
                pubrel = MQTTProtocol.build_pubrel(packet_id)
                writer.write(pubrel)
                await writer.drain()
                
                logger.debug(f"Sent PUBREL for packet_id={packet_id}")
        except Exception as e:
            logger.error(f"Error handling PUBREC: {e}")
    
    async def _handle_pubrel(self, reader: asyncio.StreamReader,
                            writer: asyncio.StreamWriter,
                            client: MQTTClient,
                            remaining_length: int):
        """Handle PUBREL message (QoS 2 flow) - respond with PUBCOMP"""
        try:
            if remaining_length >= 2:
                data = await reader.readexactly(remaining_length)
                packet_id = struct.unpack('>H', data[:2])[0]
                
                # Send PUBCOMP
                pubcomp = MQTTProtocol.build_pubcomp(packet_id)
                writer.write(pubcomp)
                await writer.drain()
                
                # Remove from pending if exists
                if client._session:
                    client._session.pending_pubrel.pop(packet_id, None)
                
                # Decrement flow control (QoS 2 complete)
                client_sock = writer.get_extra_info('socket')
                if client_sock in self._flow_control:
                    in_flight, receive_max = self._flow_control[client_sock]
                    self._flow_control[client_sock] = (max(0, in_flight - 1), receive_max)
                
                logger.debug(f"Sent PUBCOMP for packet_id={packet_id}")
        except Exception as e:
            logger.error(f"Error handling PUBREL: {e}")
    
    async def _deliver_retained_messages(self, subscribed_topic: str,
                                         client_sock: socket.socket,
                                         writer: asyncio.StreamWriter,
                                         qos: int):
        """Deliver retained messages matching the subscribed topic"""
        for topic, (payload, msg_qos, retain) in self._retained_messages.items():
            # Use the minimum QoS between subscription and retained message
            delivery_qos = min(qos, msg_qos)
            
            # Check if the retained topic matches the subscription pattern
            if topic_matches(subscribed_topic, topic):
                try:
                    publish_msg = MQTTProtocol.build_publish(topic, payload, None, delivery_qos, retain=True)
                    writer.write(publish_msg)
                    await writer.drain()
                    logger.debug(f"Delivered retained message for topic: {topic} to subscriber")
                except Exception as e:
                    logger.error(f"Error delivering retained message: {e}")
    
    async def _keepalive_monitor(self, client_sock: socket.socket, keepalive_seconds: int):
        """Monitor client keepalive and disconnect if inactive"""
        try:
            while self._running and client_sock in self._client_keepalive:
                # Wait for keepalive interval + 50% buffer
                await asyncio.sleep(keepalive_seconds * 1.5)
                
                if client_sock not in self._client_keepalive:
                    break
                
                last_activity, keepalive_sec, _ = self._client_keepalive[client_sock]
                current_time = asyncio.get_event_loop().time()
                time_since_activity = current_time - last_activity
                
                # If client has been inactive for more than 1.5x keepalive, disconnect
                if time_since_activity > (keepalive_sec * 1.5):
                    logger.warning(f"Client {client_sock} keepalive timeout - disconnecting")
                    if client_sock in self._clients:
                        _, writer = self._clients[client_sock]
                        try:
                            writer.close()
                            await writer.wait_closed()
                        except Exception:
                            pass
                    break
        except asyncio.CancelledError:
            logger.debug("Keepalive monitor cancelled")
        except Exception as e:
            logger.error(f"Error in keepalive monitor: {e}")
    
    def get_metrics(self) -> Dict[str, Any]:
        """
        Get server metrics.
        
        Returns:
            Dictionary with metrics including:
            - total_connections: Total connections ever accepted
            - current_connections: Current active connections
            - total_messages_published: Total PUBLISH messages received
            - total_messages_received: Same as published (alias)
            - total_subscriptions: Total subscriptions created
            - total_unsubscriptions: Total unsubscriptions
            - retained_messages_count: Number of retained messages
            - active_subscriptions_count: Number of active topic subscriptions
        """
        metrics = self._metrics.copy()
        metrics['retained_messages_count'] = len(self._retained_messages)
        metrics['active_subscriptions_count'] = len(self._topic_subscriptions)
        metrics['connections_per_ip'] = dict(self._connections_per_ip)
        return metrics
    
    def health_check(self) -> Dict[str, Any]:
        """
        Perform health check of the server.
        
        Returns:
            Dictionary with health status:
            - status: "healthy" or "degraded"
            - running: Whether server is running
            - connections: Current connection count
            - max_connections: Maximum connections (None if unlimited)
            - redis_connected: Whether Redis is connected (if enabled)
            - errors: List of any health issues
        """
        health = {
            'status': 'healthy',
            'running': self._running,
            'connections': self._connection_count,
            'max_connections': self._max_connections,
            'redis_connected': False,
            'errors': []
        }
        
        # Check Redis connection
        if self.use_redis:
            health['redis_connected'] = self._redis_client is not None
            if not health['redis_connected']:
                health['errors'].append("Redis connection not available")
                health['status'] = 'degraded'
        
        # Check connection limits
        if self._max_connections and self._connection_count >= self._max_connections:
            health['errors'].append(f"Connection limit reached: {self._connection_count}/{self._max_connections}")
            health['status'] = 'degraded'
        
        return health
    
    async def shutdown(self, timeout: float = 30.0):
        """
        Gracefully shutdown the server.
        
        Args:
            timeout: Maximum time to wait for connections to close (seconds)
        """
        logger.info("Starting graceful shutdown...")
        self._running = False
        
        # Set shutdown event
        if self._shutdown_event:
            self._shutdown_event.set()
        else:
            self._shutdown_event = asyncio.Event()
            self._shutdown_event.set()
        
        # Close all client connections gracefully (TCP server)
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            logger.info("TCP server socket closed")
        
        # Wait for connections to close (with timeout)
        start_time = asyncio.get_event_loop().time()
        while self._connection_count > 0:
            elapsed = asyncio.get_event_loop().time() - start_time
            if elapsed >= timeout:
                logger.warning(f"Shutdown timeout reached. {self._connection_count} connections still active.")
                break
            await asyncio.sleep(0.1)
        
        # Cleanup Redis
        await self._disconnect_redis()
        
        # Stop QUIC server
        if self._quic_server:
            await self._quic_server.stop()
        
        logger.info("Graceful shutdown complete")
    
    async def _start_server(self):
        """Start the async server"""
        # Initialize shutdown event
        self._shutdown_event = asyncio.Event()
        
        # Connect to Redis (if enabled)
        if self.use_redis:
            try:
                await self._connect_redis()
            except Exception as e:
                logger.error(f"Failed to connect to Redis: {e}")
                logger.warning("Falling back to direct routing (no Redis)")
                self.use_redis = False
        else:
            logger.info("Using direct client routing (no Redis - lower latency for single server)")
        
        # Start session cleanup task (periodically clean expired sessions)
        asyncio.create_task(self._session_cleanup_task())
        
        # Start QUIC server (if enabled)
        if self.enable_quic and MQTTQuicServer:
            try:
                self._quic_server = MQTTQuicServer(
                    host=self.host,
                    port=self.quic_port,
                    certfile=self.quic_certfile,
                    keyfile=self.quic_keyfile,
                )
                self._quic_server.set_mqtt_handler(self._handle_client)
                await self._quic_server.start()
                # Log message depends on which implementation is used
                if NGTCP2_AVAILABLE:
                    logger.info(f"MQTT over QUIC (ngtcp2) listening on {self.host}:{self.quic_port}")
                else:
                    logger.info(f"MQTT over QUIC server listening on {self.host}:{self.quic_port} (UDP/QUIC - Pure Python)")
                    logger.info("Note: Using simplified QUIC implementation. For production, install ngtcp2.")
            except Exception as e:
                logger.error(f"Failed to start QUIC server: {e}")
                if not self.enable_tcp:
                    # If TCP is disabled and QUIC fails, we can't continue
                    raise RuntimeError("QUIC server failed to start and TCP is disabled. Cannot start server.")
                logger.warning("Continuing with TCP only")
                self.enable_quic = False
        
        # Create TCP server (if enabled)
        self._server = None
        if self.enable_tcp:
            self._server = await asyncio.start_server(
                self._handle_client,
                self.host,
                self.port,
                ssl=self.ssl_context
            )
            
            protocol = "MQTTS" if self.ssl_context else "MQTT"
            logger.info(f"{protocol} server listening on {self.host}:{self.port}")
        
        # Log routing mode
        if self.use_redis and self._redis_client:
            logger.info("Redis pub/sub backend: ENABLED (for multi-server scaling)")
        elif not self.use_redis:
            logger.info("Direct routing: ENABLED (lower latency, single server)")
        
        # Server loop - wait for either TCP or QUIC (or both)
        try:
            if self._server:
                # If TCP is enabled, serve forever
                async with self._server:
                    await self._server.serve_forever()
            else:
                # QUIC-only mode: keep the event loop running
                logger.info("Running in QUIC-only mode. Server is ready.")
                while self._running:
                    await asyncio.sleep(1)
        finally:
            # Cleanup QUIC server
            if self._quic_server:
                await self._quic_server.stop()
            # Cleanup Redis on shutdown
            await self._disconnect_redis()
    
    def run(self, host: Optional[str] = None, port: Optional[int] = None,
           ssl_context: Optional[ssl.SSLContext] = None):
        """
        Run the MQTT server with Redis pub/sub backend (blocking).
        
        Args:
            host: Override host (default: use instance host)
            port: Override port (default: use instance port)
            ssl_context: Override SSL context (default: use instance ssl_context)
        """
        if host:
            self.host = host
        if port:
            self.port = port
        if ssl_context:
            self.ssl_context = ssl_context
        
        self._running = True
        
        try:
            asyncio.run(self._start_server())
        except KeyboardInterrupt:
            logger.info("Server stopped by user")
        finally:
            self._running = False
            # Ensure Redis is disconnected
            if self._redis_client:
                try:
                    asyncio.run(self._disconnect_redis())
                except Exception as e:
                    logger.error(f"Error disconnecting Redis: {e}")
    
    async def _session_cleanup_task(self):
        """Periodically clean up expired sessions"""
        while self._running:
            try:
                await asyncio.sleep(60)  # Check every minute
                removed = self._session_manager.cleanup_expired_sessions()
                if removed > 0:
                    logger.info(f"Cleaned up {removed} expired session(s)")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in session cleanup: {e}")
                await asyncio.sleep(60)
    
    async def publish(self, topic: str, payload: bytes, qos: int = 0,
                     retain: bool = False) -> bool:
        """
        Publish a message to all subscribed clients.
        
        Note: This is a simple implementation. A full MQTT broker would
        maintain topic subscriptions and route messages accordingly.
        """
        message = MQTTProtocol.build_publish(topic, payload, None, qos, retain)
        
        # Send to all connected clients
        for client_sock, client in list(self._clients.items()):
            try:
                # In a real implementation, we'd maintain writer objects
                # For now, this is a placeholder
                logger.debug(f"Would publish to {client}")
            except Exception as e:
                logger.error(f"Error publishing to {client}: {e}")
        
        return True
