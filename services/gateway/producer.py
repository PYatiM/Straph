"""Event producer abstraction and an in-memory implementation for tests and local runs."""

from __future__ import annotations

import asyncio
import logging
import threading
from collections import deque
from typing import Any, Protocol, runtime_checkable

from confluent_kafka import KafkaError, KafkaException, Message, Producer

from services.gateway.models import APIEventPayload

log = logging.getLogger("straph.gateway.producer")


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


class KafkaEventProducer(EventProducerProtocol):
    """Idempotent Kafka producer. Delivery failures are logged, never raised."""

    def __init__(
        self,
        bootstrap_servers: str,
        topic: str,
        flush_timeout: float = 10.0,
        extra_config: dict[str, Any] | None = None,
    ) -> None:
        config: dict[str, Any] = {
            "bootstrap.servers": bootstrap_servers,
            "acks": "all",
            "retries": 3,
            "enable.idempotence": True,
            **(extra_config or {}),
        }
        self._producer = Producer(config)
        self._topic = topic
        self._flush_timeout = flush_timeout
        self._closed = False

    @staticmethod
    def _on_delivery(err: KafkaError | None, msg: Message) -> None:
        if err is not None:
            log.error(
                "kafka delivery failed topic=%s key=%r error=%s",
                msg.topic(),
                msg.key(),
                err,
            )

    async def produce_event(self, key: str, event: APIEventPayload) -> bool:
        """Enqueue the event; return False (never raise) if it could not be queued."""
        if self._closed:
            return False
        try:
            self._producer.produce(
                self._topic,
                key=key.encode("utf-8"),
                value=event.to_contract_json().encode("utf-8"),
                on_delivery=self._on_delivery,
            )
            self._producer.poll(0)
        except (BufferError, KafkaException, ValueError) as exc:
            log.error("kafka produce failed topic=%s key=%r error=%s", self._topic, key, exc)
            return False
        return True

    async def close(self) -> None:
        """Flush outstanding messages (off the event loop) and stop accepting events."""
        self._closed = True
        try:
            remaining = await asyncio.to_thread(self._producer.flush, self._flush_timeout)
        except KafkaException as exc:
            log.error("kafka flush failed error=%s", exc)
            return
        if remaining:
            log.error("kafka close left %d undelivered message(s)", remaining)
