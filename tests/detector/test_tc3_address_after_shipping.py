"""Tests for TC3 address-after-shipping fixtures and schema compliance."""

import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from tests.detector.fixtures.tc3_address_after_shipping import (
    get_tc3_events,
    get_tc3_expected_alerts,
)

ROOT: Path = Path(__file__).resolve().parents[2]
EVENT_SCHEMA_PATH: Path = ROOT / "contracts" / "event.schema.json"
ALERT_SCHEMA_PATH: Path = ROOT / "contracts" / "alert.schema.json"


def _validator(path: Path) -> Draft202012Validator:
    schema: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


@pytest.fixture
def event_validator() -> Draft202012Validator:
    """Provide a validator for event.schema.json."""
    return _validator(EVENT_SCHEMA_PATH)


@pytest.fixture
def alert_validator() -> Draft202012Validator:
    """Provide a validator for alert.schema.json."""
    return _validator(ALERT_SCHEMA_PATH)


def test_tc3_sequence_structure() -> None:
    """Assert TC3 ships order 1003 (7 legal calls) then edits the address."""
    events: list[dict[str, Any]] = get_tc3_events()
    specs: list[tuple[str, str, str, int]] = [
        ("POST", "/cart/items", "customer", 200),
        ("POST", "/orders", "customer", 201),
        ("PUT", "/orders/1003/address", "customer", 200),
        ("POST", "/orders/1003/payment", "customer", 200),
        ("POST", "/payments/webhook", "payment-gateway", 200),
        ("POST", "/orders/1003/fulfill", "warehouse", 200),
        ("POST", "/orders/1003/ship", "warehouse", 200),
        ("PUT", "/orders/1003/address", "customer", 200),
    ]
    assert len(events) == len(specs)
    for event, (method, path, role, status) in zip(events, specs, strict=True):
        assert event["method"] == method
        assert event["path_template"] == path
        assert event["role"] == role
        assert event["status"] == status
    assert len({e["event_id"] for e in events}) == len(events)


@pytest.mark.parametrize("event", get_tc3_events())
def test_each_tc3_event_validates_against_event_schema(
    event_validator: Draft202012Validator,
    event: dict[str, Any],
) -> None:
    """Assert every TC3 event conforms to event.schema.json."""
    errors: list[Any] = list(event_validator.iter_errors(event))
    assert errors == [], f"Invalid event {event['event_id']}: {errors}"


def test_tc3_expected_alert_content() -> None:
    """Assert the single alert is an accepted R3 OUT_OF_ORDER on the final event."""
    alerts: list[dict[str, Any]] = get_tc3_expected_alerts()
    assert len(alerts) == 1
    alert: dict[str, Any] = alerts[0]
    assert alert["label"] == "OUT_OF_ORDER"
    assert alert["rule"] == "R3"
    assert alert["severity"] == "accepted"
    assert alert["observed"]["operation"] == "PUT /orders/{id}/address"
    assert alert["observed"]["state"] == "SHIPPED"
    assert alert["missing"] == []
    assert alert["event_id"] == get_tc3_events()[-1]["event_id"]


def test_tc3_expected_alert_validates_against_alert_schema(
    alert_validator: Draft202012Validator,
) -> None:
    """Assert the expected TC3 alert conforms to alert.schema.json."""
    for alert in get_tc3_expected_alerts():
        errors: list[Any] = list(alert_validator.iter_errors(alert))
        assert errors == [], f"Invalid alert {alert['alert_id']}: {errors}"
