"""
Thread-Safe Data Structures for No-GIL Python

These data structures are designed for Python 3.13+ with --disable-gil flag
or Python 3.14+ where no-GIL is default. They use locks for thread safety
while leveraging true parallelism.
"""

from threading import RLock
from typing import Dict, Set, Any, Optional, List, Tuple
import socket


class ThreadSafeDict:
    """Thread-safe dictionary optimized for no-GIL Python"""
    
    def __init__(self):
        self._lock = RLock()
        self._data: Dict = {}
    
    def __getitem__(self, key):
        with self._lock:
            return self._data[key]
    
    def __setitem__(self, key, value):
        with self._lock:
            self._data[key] = value
    
    def __delitem__(self, key):
        with self._lock:
            del self._data[key]
    
    def __contains__(self, key):
        with self._lock:
            return key in self._data
    
    def __len__(self):
        with self._lock:
            return len(self._data)
    
    def get(self, key, default=None):
        with self._lock:
            return self._data.get(key, default)
    
    def items(self):
        with self._lock:
            return list(self._data.items())
    
    def keys(self):
        with self._lock:
            return list(self._data.keys())
    
    def values(self):
        with self._lock:
            return list(self._data.values())
    
    def pop(self, key, default=None):
        with self._lock:
            return self._data.pop(key, default)
    
    def clear(self):
        with self._lock:
            self._data.clear()


class ThreadSafeSet:
    """Thread-safe set optimized for no-GIL Python"""
    
    def __init__(self):
        self._lock = RLock()
        self._data: Set = set()
    
    def add(self, item):
        with self._lock:
            self._data.add(item)
    
    def remove(self, item):
        with self._lock:
            self._data.remove(item)
    
    def discard(self, item):
        with self._lock:
            self._data.discard(item)
    
    def __contains__(self, item):
        with self._lock:
            return item in self._data
    
    def __len__(self):
        with self._lock:
            return len(self._data)
    
    def update(self, items):
        with self._lock:
            self._data.update(items)
    
    def copy(self):
        with self._lock:
            return self._data.copy()


class ThreadSafeTopicTrie:
    """
    Thread-safe Trie for efficient topic matching.
    
    Supports MQTT topic patterns:
    - Exact match: "sensors/temperature"
    - Single-level wildcard: "sensors/+/room1"
    - Multi-level wildcard: "sensors/#"
    """
    
    def __init__(self):
        self._lock = RLock()
        self._trie: Dict = {}
    
    def insert(self, topic: str, client_info: Any):
        """
        Insert topic subscription.
        
        Args:
            topic: MQTT topic pattern (e.g., "sensors/+/temperature")
            client_info: Client information (socket, writer, etc.)
        """
        with self._lock:
            parts = topic.split('/')
            node = self._trie
            
            for part in parts:
                if part not in node:
                    node[part] = {}
                node = node[part]
            
            # Store clients at leaf node
            if 'clients' not in node:
                node['clients'] = ThreadSafeSet()
            node['clients'].add(client_info)
    
    def remove(self, topic: str, client_info: Any):
        """Remove topic subscription"""
        with self._lock:
            parts = topic.split('/')
            node = self._trie
            
            for part in parts:
                if part not in node:
                    return  # Topic not found
                node = node[part]
            
            if 'clients' in node:
                node['clients'].discard(client_info)
                if len(node['clients']) == 0:
                    del node['clients']
    
    def find_matching(self, publish_topic: str) -> Set:
        """
        Find all matching subscriptions for a publish topic.
        
        Args:
            publish_topic: Topic being published (e.g., "sensors/device1/temperature")
        
        Returns:
            Set of client_info objects that match
        """
        with self._lock:
            matches = set()
            parts = publish_topic.split('/')
            self._find_recursive(self._trie, parts, 0, matches)
            return matches
    
    def _find_recursive(self, node: Dict, parts: List[str], index: int, matches: Set):
        """Recursive matching with wildcard support"""
        if index >= len(parts):
            # Reached end of topic path
            if 'clients' in node:
                matches.update(node['clients']._data)
            return
        
        part = parts[index]
        
        # Exact match
        if part in node:
            self._find_recursive(node[part], parts, index + 1, matches)
        
        # Single-level wildcard (+)
        if '+' in node:
            self._find_recursive(node['+'], parts, index + 1, matches)
        
        # Multi-level wildcard (#) - matches everything from here
        if '#' in node:
            if 'clients' in node['#']:
                matches.update(node['#']['clients']._data)
    
    def clear(self):
        """Clear all subscriptions"""
        with self._lock:
            self._trie.clear()


class ThreadSafeConnectionPool:
    """Thread-local connection pools with thread affinity"""
    
    def __init__(self, num_threads: int = 24):
        self.num_threads = num_threads
        self._pools: List[Dict[socket.socket, Any]] = [{} for _ in range(num_threads)]
        self._locks: List[RLock] = [RLock() for _ in range(num_threads)]
        self._counts: List[int] = [0] * num_threads
    
    def get_pool(self, thread_id: int) -> Dict:
        """Get connection pool for specific thread"""
        return self._pools[thread_id]
    
    def add_connection(self, thread_id: int, socket_obj: socket.socket, connection_info: Any):
        """Add connection to thread's pool"""
        with self._locks[thread_id]:
            self._pools[thread_id][socket_obj] = connection_info
            self._counts[thread_id] += 1
    
    def remove_connection(self, thread_id: int, socket_obj: socket.socket):
        """Remove connection from thread's pool"""
        with self._locks[thread_id]:
            if socket_obj in self._pools[thread_id]:
                del self._pools[thread_id][socket_obj]
                self._counts[thread_id] -= 1
    
    def get_thread_with_least_connections(self) -> int:
        """Get thread ID with least connections (for load balancing)"""
        return min(range(self.num_threads), key=lambda i: self._counts[i])
    
    def get_total_connections(self) -> int:
        """Get total number of connections across all threads"""
        return sum(self._counts)
    
    def get_thread_connection_count(self, thread_id: int) -> int:
        """Get connection count for specific thread"""
        return self._counts[thread_id]
