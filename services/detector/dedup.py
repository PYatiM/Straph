"""Event deduplication for Kafka at-least-once delivery."""

from __future__ import annotations

import threading
from collections import OrderedDict


class EventDeduplicator:
    """Thread-safe LRU cache for detecting duplicate event deliveries."""

    def __init__(self, max_size: int = 10000) -> None:
        self.max_size = max_size
        self._cache: OrderedDict[str, None] = OrderedDict()
        self._lock = threading.Lock()

    def is_duplicate(self, event_id: str) -> bool:
        """Check if event_id has been seen; marks it seen and returns True if duplicate."""
        if not event_id:
            return False
        with self._lock:
            if event_id in self._cache:
                self._cache.move_to_end(event_id)
                return True
            self._cache[event_id] = None
            if len(self._cache) > self.max_size:
                self._cache.popitem(last=False)
            return False

    def __contains__(self, event_id: str) -> bool:
        with self._lock:
            return event_id in self._cache

    def count(self) -> int:
        with self._lock:
            return len(self._cache)
