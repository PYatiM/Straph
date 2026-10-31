"""Tests for TC8 legal idempotent retry fixtures and schema compliance."""

import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from tests.detector.fixtures.tc8_legal_retry import (
    get_tc8_events,
    get_tc8_expected_alerts,
)

ROOT: Path = Path(__file__).resolve().parents[2]
EVENT_SCHEMA_PATH: Path = ROOT / "contracts" / "event.schema.json"


@pytest.fixture
def event_validator() -> Draft202012Validator:
    """Provide a validator for event.schema.json."""
    schema: dict[str, Any] = json.loads(EVENT_SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def test_tc8_sequence_structure() -> None:
    """Assert the path to PAYMENT_PENDING followed by a failed then successful payment."""
    events: list[dict[str, Any]] = get_tc8_events()
    specs: list[tuple[str, str, int]] = [
        ("POST", "/cart/items", 200),
        ("POST", "/orders", 201),
        ("PUT", "/orders/1008/address", 200),
        ("POST", "/orders/1008/payment", 500),
        ("POST", "/orders/1008/payment", 200),
    ]
    assert len(events) == len(specs)
    for event, (method, path, status) in zip(events, specs, strict=True):
        assert (
            event["operation"]["method"],
            event["operation"]["path_template"],
            event["status_code"],
        ) == (
            method,
            path,
            status,
        )
    assert len({e["event_id"] for e in events}) == len(events)


def test_tc8_retry_shares_idempotency_key() -> None:
    """Assert the failed attempt and the retry share one idempotency key."""
    events: list[dict[str, Any]] = get_tc8_events()
    failed, retry = events[-2], events[-1]
    assert failed["idempotency_key"] == retry["idempotency_key"] == "idemp-999"
    assert failed["status_code"] == 500
    assert retry["status_code"] == 200
    assert all("idempotency_key" not in e for e in events[:-2])


def test_tc8_expected_alerts_empty() -> None:
    """Assert idempotent retries produce zero alerts."""
    assert get_tc8_expected_alerts() == []


@pytest.mark.parametrize("event", get_tc8_events())
def test_each_tc8_event_validates_against_event_schema(
    event_validator: Draft202012Validator,
    event: dict[str, Any],
) -> None:
    """Assert every TC8 event conforms to event.schema.json."""
    errors: list[Any] = list(event_validator.iter_errors(event))
    assert errors == [], f"Invalid event {event['event_id']}: {errors}"
