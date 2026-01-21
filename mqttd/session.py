"""
MQTT Session Management

Handles session state per ClientID according to MQTT 5.0 specification.
Reference: https://docs.oasis-open.org/mqtt/mqtt/v5.0/mqtt-v5.0.html
"""

import time
from typing import Dict, Set, Optional, Any, Tuple
from dataclasses import dataclass, field
from enum import IntEnum
import socket


class SessionState(IntEnum):
    """Session state"""
    ACTIVE = 0
    DISCONNECTED = 1
    EXPIRED = 2


@dataclass
class SessionSubscription:
    """Represents a subscription within a session"""
    topic: str
    qos: int
    subscription_id: Optional[int] = None


@dataclass
class Session:
    """
    Represents an MQTT session for a ClientID.
    
    According to MQTT 5.0, sessions are identified by ClientID and persist
    based on Session Expiry Interval.
    """
    client_id: str
    created_at: float = field(default_factory=time.time)
    expiry_interval: int = 0  # Session Expiry Interval in seconds (0 = expire on disconnect)
    expiry_time: Optional[float] = None  # Absolute time when session expires
    clean_start: bool = True
    
    # Active connection
    active_socket: Optional[socket.socket] = None
    active_writer: Optional[Any] = None  # asyncio.StreamWriter
    
    # Session state
    state: SessionState = SessionState.ACTIVE
    
    # Subscriptions (persist across connections if Session Expiry > 0)
    subscriptions: Dict[str, SessionSubscription] = field(default_factory=dict)
    
    # QoS 1/2 messages in flight (for session persistence)
    pending_puback: Dict[int, Any] = field(default_factory=dict)  # Packet ID -> message
    pending_pubrec: Dict[int, Any] = field(default_factory=dict)
    pending_pubrel: Dict[int, Any] = field(default_factory=dict)
    pending_pubcomp: Dict[int, Any] = field(default_factory=dict)
    
    # Topic aliases (MQTT 5.0)
    topic_aliases: Dict[int, str] = field(default_factory=dict)  # Alias -> Topic
    
    # Will message (if any)
    will_message: Optional[Dict[str, Any]] = None
    
    def is_expired(self) -> bool:
        """Check if session has expired"""
        if self.expiry_interval == 0:
            # Session expires immediately when connection closes
            return self.state == SessionState.DISCONNECTED
        elif self.expiry_time is None:
            return False
        else:
            return time.time() > self.expiry_time
    
    def update_expiry(self, expiry_interval: int):
        """
        Update session expiry interval.
        
        Args:
            expiry_interval: New expiry interval in seconds (0 = expire on disconnect)
        """
        self.expiry_interval = expiry_interval
        if expiry_interval == 0:
            self.expiry_time = None
        else:
            self.expiry_time = time.time() + expiry_interval
    
    def mark_disconnected(self):
        """Mark session as disconnected"""
        self.state = SessionState.DISCONNECTED
        self.active_socket = None
        self.active_writer = None
        
        # If expiry interval is 0, session expires immediately
        if self.expiry_interval == 0:
            self.state = SessionState.EXPIRED
    
    def mark_active(self, socket_obj: socket.socket, writer: Any):
        """Mark session as active with new connection"""
        self.state = SessionState.ACTIVE
        self.active_socket = socket_obj
        self.active_writer = writer
    
    def add_subscription(self, topic: str, qos: int, subscription_id: Optional[int] = None):
        """Add or update subscription"""
        self.subscriptions[topic] = SessionSubscription(
            topic=topic,
            qos=qos,
            subscription_id=subscription_id
        )
    
    def remove_subscription(self, topic: str):
        """Remove subscription"""
        self.subscriptions.pop(topic, None)
    
    def get_all_subscriptions(self) -> Dict[str, SessionSubscription]:
        """Get all active subscriptions"""
        return self.subscriptions.copy()


class SessionManager:
    """
    Manages MQTT sessions by ClientID.
    
    Handles:
    - Session creation and cleanup
    - Session takeover detection
    - Session expiry
    - Concurrent connection handling
    """
    
    def __init__(self):
        # ClientID -> Session
        self._sessions: Dict[str, Session] = {}
        
        # Socket -> ClientID (for quick lookup)
        self._socket_to_client_id: Dict[socket.socket, str] = {}
    
    def get_session(self, client_id: str) -> Optional[Session]:
        """Get session for ClientID, or None if not found/expired"""
        session = self._sessions.get(client_id)
        if session and session.is_expired():
            # Clean up expired session
            self.remove_session(client_id)
            return None
        return session
    
    def create_session(
        self,
        client_id: str,
        socket_obj: socket.socket,
        writer: Any,
        clean_start: bool = True,
        session_expiry_interval: int = 0
    ) -> Tuple[Session, Optional[Session]]:
        """
        Create or get existing session.
        
        Returns:
            Tuple of (session, old_session_if_taken_over)
        """
        old_session = self.get_session(client_id)
        old_connection_socket = None
        
        # Check for session takeover
        if old_session and old_session.state == SessionState.ACTIVE:
            if clean_start:
                # Clean Start = 1: Discard old session
                old_connection_socket = old_session.active_socket
                self.remove_session(client_id)
                old_session = None
            else:
                # Clean Start = 0: Take over old connection
                if old_session.active_socket:
                    old_connection_socket = old_session.active_socket
                    old_session.mark_disconnected()
        
        # Create or update session
        if client_id in self._sessions and not clean_start:
            # Reuse existing session
            session = self._sessions[client_id]
            if session_expiry_interval >= 0:
                session.update_expiry(session_expiry_interval)
        else:
            # Create new session
            session = Session(
                client_id=client_id,
                clean_start=clean_start,
                expiry_interval=session_expiry_interval
            )
            if session_expiry_interval > 0:
                session.update_expiry(session_expiry_interval)
            self._sessions[client_id] = session
        
        # Mark session as active
        session.mark_active(socket_obj, writer)
        
        # Update mappings
        self._socket_to_client_id[socket_obj] = client_id
        
        return session, old_session if old_connection_socket else None
    
    def remove_session(self, client_id: str):
        """Remove session completely"""
        session = self._sessions.pop(client_id, None)
        if session and session.active_socket:
            self._socket_to_client_id.pop(session.active_socket, None)
    
    def remove_connection(self, socket_obj: socket.socket) -> Optional[Session]:
        """
        Remove connection from session.
        
        Returns:
            Session if it should be preserved, None otherwise
        """
        client_id = self._socket_to_client_id.pop(socket_obj, None)
        if not client_id:
            return None
        
        session = self._sessions.get(client_id)
        if not session:
            return None
        
        # If this was the active connection, mark as disconnected
        if session.active_socket == socket_obj:
            session.mark_disconnected()
            
            # If session has expired, remove it
            if session.is_expired():
                self.remove_session(client_id)
                return None
            
            # Otherwise, preserve session (if expiry > 0)
            if session.expiry_interval > 0:
                return session
            else:
                # Expiry = 0, remove session
                self.remove_session(client_id)
                return None
        
        return session
    
    def get_client_id(self, socket_obj: socket.socket) -> Optional[str]:
        """Get ClientID for socket"""
        return self._socket_to_client_id.get(socket_obj)
    
    def cleanup_expired_sessions(self) -> int:
        """
        Clean up all expired sessions.
        
        Returns:
            Number of sessions removed
        """
        expired = []
        for client_id, session in self._sessions.items():
            if session.is_expired():
                expired.append(client_id)
        
        for client_id in expired:
            self.remove_session(client_id)
        
        return len(expired)
    
    def get_active_sessions_count(self) -> int:
        """Get count of active sessions"""
        return sum(1 for s in self._sessions.values() if s.state == SessionState.ACTIVE)
    
    def get_all_sessions_count(self) -> int:
        """Get total count of sessions (including disconnected but not expired)"""
        return len(self._sessions)
