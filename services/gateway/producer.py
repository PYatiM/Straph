"""Event producer abstraction and an in-memory implementation for tests and local runs."""

from __future__ import annotations

import threading
from collections import deque
from typing import Protocol, runtime_checkable

from services.gateway.models import APIEventPayload


def event_key(event: APIEventPayload) -> str:
    """Partition key for an event: one key per business object instance."""
    return f"{event.resource_type}:{event.resource_id}"


@runtime_checkable
class EventProducerProtocol(Protocol):
    """Publishes completed API events."""

    async def produce_event(self, key: str, event: APIEventPayload) -> bool:
        """Publish an event under key; return True when accepted."""
        ...

    async def close(self) -> None:
        """Release resources; further produce calls are rejected."""
        ...


class InMemoryEventProducer(EventProducerProtocol):
    """Thread-safe producer that keeps published (key, event) pairs in a deque."""

    def __init__(self, maxlen: int | None = None) -> None:
        self._events: deque[tuple[str, APIEventPayload]] = deque(maxlen=maxlen)
        self._lock = threading.Lock()
        self._closed = False

    async def produce_event(self, key: str, event: APIEventPayload) -> bool:
        with self._lock:
            if self._closed:
                return False
            self._events.append((key, event))
            return True

    async def close(self) -> None:
        with self._lock:
            self._closed = True

    @property
    def events(self) -> list[tuple[str, APIEventPayload]]:
        """Snapshot of published (key, event) pairs in publish order."""
        with self._lock:
            return list(self._events)
