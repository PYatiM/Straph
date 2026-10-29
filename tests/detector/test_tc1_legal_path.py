"""Tests for TC1 legal path fixtures and schema compliance."""

import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from tests.detector.fixtures.tc1_legal_path import (
    get_tc1_events,
    get_tc1_expected_alerts,
)

ROOT: Path = Path(__file__).resolve().parents[2]
EVENT_SCHEMA_PATH: Path = ROOT / "contracts" / "event.schema.json"


@pytest.fixture
def event_validator() -> Draft202012Validator:
    """Provide a validated Draft202012Validator for event.schema.json."""
    schema: dict[str, Any] = json.loads(EVENT_SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def test_tc1_sequence_structure() -> None:
    """Assert get_tc1_events() returns exactly 7 events with status codes in 200/201."""
    events: list[dict[str, Any]] = get_tc1_events()
    assert len(events) == 7

    expected_specs: list[tuple[str, str, str, int]] = [
        ("POST", "/cart/items", "customer", 200),
        ("POST", "/orders", "customer", 201),
        ("PUT", "/orders/1001/address", "customer", 200),
        ("POST", "/orders/1001/payment", "customer", 200),
        ("POST", "/payments/webhook", "payment-gateway", 200),
        ("POST", "/orders/1001/fulfill", "warehouse", 200),
        ("POST", "/orders/1001/ship", "warehouse", 200),
    ]

    for event, (method, path, role, status) in zip(events, expected_specs, strict=True):
        assert event["method"] == method
        assert event["path_template"] == path
        assert event["role"] == role
        assert event["status"] == status
        assert event["status"] in {200, 201}


def test_tc1_expected_alerts_empty() -> None:
    """Assert get_tc1_expected_alerts() returns an empty list."""
    alerts: list[dict[str, Any]] = get_tc1_expected_alerts()
    assert alerts == []
    assert len(alerts) == 0


@pytest.mark.parametrize("event", get_tc1_events())
def test_each_tc1_event_validates_against_event_schema(
    event_validator: Draft202012Validator,
    event: dict[str, Any],
) -> None:
    """Assert every event in TC1 sequence conforms strictly to event.schema.json."""
    errors: list[Any] = list(event_validator.iter_errors(event))
    assert errors == [], f"Schema validation error for event {event['event_id']}: {errors}"
