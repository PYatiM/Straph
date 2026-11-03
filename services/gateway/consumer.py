"""Asynchronous Kafka consumer dispatcher for API event ingestion and dead-letter routing."""

from __future__ import annotations

import asyncio
import json
import logging
import signal
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from confluent_kafka import Consumer, KafkaError, Producer
from jsonschema import Draft7Validator
from jsonschema.exceptions import ValidationError

log = logging.getLogger("straph.gateway.consumer")

DEFAULT_BOOTSTRAP = "localhost:9092"
DEFAULT_EVENTS_TOPIC = "api.events"
DEFAULT_DEADLETTER_TOPIC = "straph.deadletter"
DEFAULT_GROUP_ID = "straph-event-consumers"


class AsyncKafkaConsumer:
    """Asynchronous Kafka consumer with contract validation and dead-letter queue routing."""

    def __init__(
        self,
        bootstrap_servers: str = DEFAULT_BOOTSTRAP,
        topic: str = DEFAULT_EVENTS_TOPIC,
        deadletter_topic: str = DEFAULT_DEADLETTER_TOPIC,
        group_id: str = DEFAULT_GROUP_ID,
        *,
        schema_path: Path | str | None = None,
        consumer_client: Any = None,
        producer_client: Any = None,
    ) -> None:
        self.bootstrap_servers = bootstrap_servers
        self.topic = topic
        self.deadletter_topic = deadletter_topic
        self.group_id = group_id
        self.running: bool = False

        # Load event validation schema
        if schema_path is None:
            schema_path = Path(__file__).resolve().parents[2] / "contracts" / "event.schema.json"
        self.schema_path = Path(schema_path)
        with open(self.schema_path, "r", encoding="utf-8") as f:
            self._schema = json.load(f)
        self._validator = Draft7Validator(self._schema)

        # Consumer client initialization (mockable for testing)
        if consumer_client is not None:
            self._consumer = consumer_client
        else:
            self._consumer = Consumer(
                {
                    "bootstrap.servers": self.bootstrap_servers,
                    "group.id": self.group_id,
                    "auto.offset.reset": "earliest",
                    "enable.auto.commit": False,
                }
            )
            self._consumer.subscribe([self.topic])

        # Producer client for dead-letter routing (mockable for testing)
        if producer_client is not None:
            self._producer = producer_client
        else:
            self._producer = Producer({"bootstrap.servers": self.bootstrap_servers})

    def install_signal_handlers(self) -> None:
        """Register signal handlers for graceful shutdown on SIGINT and SIGTERM."""
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(sig, lambda _s, _f: self.stop())
            except (ValueError, AttributeError):
                # Signals may not be assignable outside main thread in test environments
                pass

    def stop(self) -> None:
        """Signal consumer to stop processing and close clients."""
        log.info("Stopping Kafka consumer...")
        self.running = False
        try:
            if hasattr(self._consumer, "close"):
                self._consumer.close()
        except Exception:
            log.exception("Error closing consumer client")
        try:
            if hasattr(self._producer, "flush"):
                self._producer.flush(2.0)
        except Exception:
            log.exception("Error flushing deadletter producer client")

    def process_raw_value(
        self,
        raw_val: bytes | str | None,
    ) -> tuple[bool, dict[str, Any] | None, str | None]:
        """Deserialize and validate raw message payload.

        Returns:
            (is_valid, parsed_payload_or_none, error_message_or_none)
        """
        if raw_val is None:
            return False, None, "Payload is empty (None)"

        # 1. JSON Deserialization
        try:
            if isinstance(raw_val, bytes):
                text = raw_val.decode("utf-8")
            else:
                text = str(raw_val)
            data = json.loads(text)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            return False, None, f"JSON deserialization failed: {exc!s}"

        if not isinstance(data, dict):
            return False, None, "Payload is not a JSON object"

        # 2. Schema Validation
        try:
            self._validator.validate(data)
        except ValidationError as exc:
            return False, data, f"Schema validation error: {exc.message}"

        return True, data, None

    def route_to_deadletter(
        self,
        raw_or_parsed: Any,
        error_message: str,
        original_topic: str | None = None,
    ) -> None:
        """Route unparsable or schema-invalid messages to deadletter topic with error metadata."""
        deadletter_record = {
            "error": error_message,
            "original_topic": original_topic or self.topic,
            "timestamp": datetime.now(UTC).isoformat(),
            "payload": raw_or_parsed
            if isinstance(raw_or_parsed, (dict, list, str, int, float, bool))
            else str(raw_or_parsed),
        }
        encoded = json.dumps(deadletter_record).encode("utf-8")
        if hasattr(self._producer, "produce"):
            self._producer.produce(self.deadletter_topic, encoded)
        if hasattr(self._producer, "flush"):
            self._producer.flush(1.0)
        log.warning("Routed invalid event to deadletter: %s", error_message)

    def poll_and_process_one(self, timeout: float = 1.0) -> dict[str, Any] | None:
        """Poll a single message, validate it, route to deadletter if invalid, and commit offset."""
        msg = self._consumer.poll(timeout)
        if msg is None:
            return None

        # Handle KafkaError
        if hasattr(msg, "error") and msg.error():
            err = msg.error()
            if isinstance(err, KafkaError) and err.code() == KafkaError._PARTITION_EOF:
                return None
            log.error("Kafka consumer error: %s", err)
            return None

        raw_val = msg.value() if hasattr(msg, "value") else msg
        msg_topic = msg.topic() if hasattr(msg, "topic") else self.topic

        is_valid, payload, error_msg = self.process_raw_value(raw_val)

        if is_valid and payload is not None:
            # Commit processed valid event offset
            if hasattr(self._consumer, "commit"):
                self._consumer.commit(message=msg, asynchronous=False)
            return payload

        # Route invalid payload to deadletter queue
        self.route_to_deadletter(
            raw_or_parsed=payload if payload is not None else raw_val,
            error_message=error_msg or "Unknown validation failure",
            original_topic=msg_topic,
        )
        if hasattr(self._consumer, "commit"):
            self._consumer.commit(message=msg, asynchronous=False)
        return None

    async def run(
        self,
        handler: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
        poll_interval: float = 0.1,
    ) -> None:
        """Continuously poll messages until stop() is invoked."""
        self.running = True
        while self.running:
            payload = await asyncio.to_thread(self.poll_and_process_one, timeout=poll_interval)
            if payload and handler:
                try:
                    await handler(payload)
                except Exception:
                    log.exception("Handler failed while processing event")
            await asyncio.sleep(0.01)
