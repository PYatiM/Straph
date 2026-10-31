"""Tests for KafkaEventProducer with confluent_kafka.Producer mocked."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any
from unittest import mock

import pytest
from confluent_kafka import KafkaError, KafkaException

from services.gateway import producer as mod
from services.gateway.models import APIEventPayload
from services.gateway.producer import EventProducerProtocol, KafkaEventProducer, event_key

VALID: dict[str, Any] = json.loads(
    (Path(__file__).resolve().parents[2] / "contracts" / "examples" / "event_valid.json").read_text(
        encoding="utf-8"
    )
)
EVENT = APIEventPayload.model_validate(VALID)


@pytest.fixture
def kafka() -> Any:
    with mock.patch.object(mod, "Producer") as cls:
        yield cls


def make() -> KafkaEventProducer:
    return KafkaEventProducer("broker:9092", "api.events")


def test_idempotent_delivery_config(kafka: mock.MagicMock) -> None:
    make()
    config = kafka.call_args.args[0]
    assert config["acks"] == "all"
    assert config["retries"] == 3
    assert config["enable.idempotence"] is True
    assert config["bootstrap.servers"] == "broker:9092"


def test_adheres_to_protocol(kafka: mock.MagicMock) -> None:
    assert isinstance(make(), EventProducerProtocol)


def test_key_and_value_propagation(kafka: mock.MagicMock) -> None:
    p = make()
    assert asyncio.run(p.produce_event(event_key(EVENT), EVENT)) is True
    inner = kafka.return_value
    args, kwargs = inner.produce.call_args
    assert args == ("api.events",)
    assert kwargs["key"] == b"Order:1042"
    assert kwargs["value"] == EVENT.to_contract_json().encode("utf-8")
    assert json.loads(kwargs["value"]) == VALID
    assert callable(kwargs["on_delivery"])
    inner.poll.assert_called_with(0)


def test_non_ascii_key_encoded_utf8(kafka: mock.MagicMock) -> None:
    asyncio.run(make().produce_event("Ordér:é", EVENT))
    assert kafka.return_value.produce.call_args.kwargs["key"] == "Ordér:é".encode()


def test_delivery_error_is_logged(kafka: mock.MagicMock, caplog: pytest.LogCaptureFixture) -> None:
    asyncio.run(make().produce_event("Order:1042", EVENT))
    callback = kafka.return_value.produce.call_args.kwargs["on_delivery"]
    msg = mock.MagicMock()
    msg.topic.return_value = "api.events"
    msg.key.return_value = b"Order:1042"
    with caplog.at_level(logging.ERROR, logger="straph.gateway.producer"):
        callback(KafkaError(KafkaError._MSG_TIMED_OUT), msg)
    assert "delivery failed" in caplog.text
    assert "Order:1042" in caplog.text


def test_successful_delivery_logs_nothing(
    kafka: mock.MagicMock, caplog: pytest.LogCaptureFixture
) -> None:
    asyncio.run(make().produce_event("k", EVENT))
    callback = kafka.return_value.produce.call_args.kwargs["on_delivery"]
    with caplog.at_level(logging.ERROR, logger="straph.gateway.producer"):
        callback(None, mock.MagicMock())
    assert caplog.text == ""


@pytest.mark.parametrize(
    "error",
    [BufferError("queue full"), KafkaException(KafkaError(KafkaError._TRANSPORT))],
)
def test_produce_errors_logged_not_raised(
    kafka: mock.MagicMock, caplog: pytest.LogCaptureFixture, error: Exception
) -> None:
    kafka.return_value.produce.side_effect = error
    with caplog.at_level(logging.ERROR, logger="straph.gateway.producer"):
        assert asyncio.run(make().produce_event("k", EVENT)) is False
    assert "produce failed" in caplog.text


def test_close_flushes_and_rejects_further_events(kafka: mock.MagicMock) -> None:
    kafka.return_value.flush.return_value = 0
    p = make()

    async def run() -> bool:
        await p.close()
        return await p.produce_event("k", EVENT)

    assert asyncio.run(run()) is False
    kafka.return_value.flush.assert_called_once_with(10.0)
    kafka.return_value.produce.assert_not_called()


def test_close_logs_undelivered(kafka: mock.MagicMock, caplog: pytest.LogCaptureFixture) -> None:
    kafka.return_value.flush.return_value = 2
    with caplog.at_level(logging.ERROR, logger="straph.gateway.producer"):
        asyncio.run(make().close())
    assert "2 undelivered" in caplog.text
