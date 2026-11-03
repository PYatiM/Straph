"""Unit tests for AsyncKafkaConsumer error routing, validation, and graceful shutdown."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from services.gateway.consumer import AsyncKafkaConsumer

ROOT_DIR = Path(__file__).resolve().parents[2]
VALID_EVENT_PATH = ROOT_DIR / "contracts" / "examples" / "event_valid.json"


class MockKafkaMessage:
    """Mock representing confluent_kafka.Message."""

    def __init__(self, value: bytes | str | None, topic: str = "api.events") -> None:
        self._value = value
        self._topic = topic

    def value(self) -> bytes | str | None:
        return self._value

    def topic(self) -> str:
        return self._topic

    def error(self) -> Any:
        return None


class MockKafkaConsumerClient:
    """Mock confluent_kafka.Consumer tracking poll, commit, and close calls."""

    def __init__(self, messages: list[MockKafkaMessage] | None = None) -> None:
        self._messages: list[MockKafkaMessage] = messages or []
        self.committed_messages: list[MockKafkaMessage] = []
        self.closed: bool = False
        self.subscribed_topics: list[list[str]] = []

    def subscribe(self, topics: list[str]) -> None:
        self.subscribed_topics.append(topics)

    def poll(self, timeout: float = 1.0) -> MockKafkaMessage | None:
        if self._messages:
            return self._messages.pop(0)
        return None

    def commit(self, message: Any = None, asynchronous: bool = False) -> None:
        if message:
            self.committed_messages.append(message)

    def close(self) -> None:
        self.closed = True


class MockKafkaProducerClient:
    """Mock confluent_kafka.Producer tracking produce and flush calls."""

    def __init__(self) -> None:
        self.produced_messages: list[tuple[str, bytes]] = []
        self.flushed: bool = False

    def produce(self, topic: str, value: bytes) -> None:
        self.produced_messages.append((topic, value))

    def flush(self, timeout: float = 1.0) -> int:
        self.flushed = True
        return 0


@pytest.fixture
def valid_event_bytes() -> bytes:
    with open(VALID_EVENT_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    return json.dumps(data).encode("utf-8")


def test_valid_message_processing_and_offset_commit(valid_event_bytes: bytes) -> None:
    """Verify schema-valid events are returned without deadletter routing, and offsets committed."""
    msg = MockKafkaMessage(value=valid_event_bytes)
    mock_consumer = MockKafkaConsumerClient([msg])
    mock_producer = MockKafkaProducerClient()

    consumer = AsyncKafkaConsumer(
        consumer_client=mock_consumer,
        producer_client=mock_producer,
    )

    result = consumer.poll_and_process_one()
    assert result is not None
    assert result["resource_type"] == "Order"
    assert result["resource_id"] == "1042"

    # Verify offset committed
    assert len(mock_consumer.committed_messages) == 1
    # Verify no deadletter message produced
    assert len(mock_producer.produced_messages) == 0


def test_malformed_json_routed_to_deadletter() -> None:
    """Verify malformed non-JSON payloads are routed to deadletter topic with metadata."""
    corrupt_bytes = b"not a json document {broken"
    msg = MockKafkaMessage(value=corrupt_bytes)
    mock_consumer = MockKafkaConsumerClient([msg])
    mock_producer = MockKafkaProducerClient()

    consumer = AsyncKafkaConsumer(
        consumer_client=mock_consumer,
        producer_client=mock_producer,
    )

    result = consumer.poll_and_process_one()
    assert result is None

    # Verify routed to deadletter
    assert len(mock_producer.produced_messages) == 1
    topic, dlq_bytes = mock_producer.produced_messages[0]
    assert topic == "straph.deadletter"

    dlq_record = json.loads(dlq_bytes.decode("utf-8"))
    assert "JSON deserialization failed" in dlq_record["error"]
    assert dlq_record["original_topic"] == "api.events"
    assert "not a json document" in dlq_record["payload"]

    # Offset must be committed to avoid reprocessing poisonous record indefinitely
    assert len(mock_consumer.committed_messages) == 1


def test_schema_invalid_payload_routed_to_deadletter() -> None:
    """Verify valid JSON violating contracts/event.schema.json is routed to deadletter."""
    invalid_event = {
        "event_id": "not-a-valid-uuid",
        "some_extra_garbage": True,
    }
    msg = MockKafkaMessage(value=json.dumps(invalid_event).encode("utf-8"))
    mock_consumer = MockKafkaConsumerClient([msg])
    mock_producer = MockKafkaProducerClient()

    consumer = AsyncKafkaConsumer(
        consumer_client=mock_consumer,
        producer_client=mock_producer,
    )

    result = consumer.poll_and_process_one()
    assert result is None

    assert len(mock_producer.produced_messages) == 1
    topic, dlq_bytes = mock_producer.produced_messages[0]
    assert topic == "straph.deadletter"

    dlq_record = json.loads(dlq_bytes.decode("utf-8"))
    assert "Schema validation error" in dlq_record["error"]
    assert dlq_record["payload"]["event_id"] == "not-a-valid-uuid"

    # Offset must still be committed
    assert len(mock_consumer.committed_messages) == 1


@pytest.mark.anyio
async def test_graceful_shutdown(valid_event_bytes: bytes) -> None:
    """Verify stop() cleanly closes consumer and commits offsets."""
    messages = [
        MockKafkaMessage(value=valid_event_bytes),
        MockKafkaMessage(value=valid_event_bytes),
    ]
    mock_consumer = MockKafkaConsumerClient(messages)
    mock_producer = MockKafkaProducerClient()

    consumer = AsyncKafkaConsumer(
        consumer_client=mock_consumer,
        producer_client=mock_producer,
    )

    processed: list[dict[str, Any]] = []

    async def handler(payload: dict[str, Any]) -> None:
        processed.append(payload)
        if len(processed) >= 2:
            consumer.stop()

    run_task = asyncio.create_task(consumer.run(handler=handler, poll_interval=0.01))
    await asyncio.wait_for(run_task, timeout=2.0)

    assert len(processed) == 2
    assert consumer.running is False
    assert mock_consumer.closed is True
    assert mock_producer.flushed is True


def test_install_signal_handlers() -> None:
    """Verify install_signal_handlers does not raise."""
    mock_consumer = MockKafkaConsumerClient([])
    mock_producer = MockKafkaProducerClient()
    consumer = AsyncKafkaConsumer(
        consumer_client=mock_consumer,
        producer_client=mock_producer,
    )
    # Calling install_signal_handlers should not throw exceptions
    consumer.install_signal_handlers()
