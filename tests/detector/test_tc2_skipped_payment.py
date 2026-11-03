"""Tests for TC2 skipped-payment fixtures and schema compliance."""

import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from tests.detector.fixtures.tc2_skipped_payment import (
    get_tc2_events,
    get_tc2_expected_alerts,
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


def test_tc2_sequence_structure() -> None:
    """Assert TC2 has 3 events for order 1002 with the expected operations."""
    events: list[dict[str, Any]] = get_tc2_events()
    specs: list[tuple[str, str, str, int]] = [
        ("POST", "/orders", "customer", 201),
        ("PUT", "/orders/1002/address", "customer", 200),
        ("POST", "/orders/1002/fulfill", "warehouse", 200),
    ]
    assert len(events) == len(specs)
    for event, (method, path, role, status) in zip(events, specs, strict=True):
        assert event["operation"]["method"] == method
        assert event["operation"]["path_template"] == path
        assert event["subject"]["role"] == role
        assert event["status_code"] == status
        assert event["resource_type"] == "Order"
        assert event["resource_id"] == "1002"


@pytest.mark.parametrize("event", get_tc2_events())
def test_each_tc2_event_validates_against_event_schema(
    event_validator: Draft202012Validator,
    event: dict[str, Any],
) -> None:
    """Assert every TC2 event conforms to event.schema.json."""
    errors: list[Any] = list(event_validator.iter_errors(event))
    assert errors == [], f"Invalid event {event['event_id']}: {errors}"


def test_tc2_expected_alert_content() -> None:
    """Assert the single expected alert is an accepted R2 SKIPPED alert."""
    alerts: list[dict[str, Any]] = get_tc2_expected_alerts()
    assert len(alerts) == 1
    alert: dict[str, Any] = alerts[0]
    assert alert["label"] == "SKIPPED"
    assert alert["rule"] == "R2"
    assert alert["severity"] == "accepted"
    assert alert["missing"] == ["POST /orders/{id}/payment", "POST /payments/webhook"]
    assert alert["event_id"] == get_tc2_events()[-1]["event_id"]


def test_tc2_expected_alert_validates_against_alert_schema(
    alert_validator: Draft202012Validator,
) -> None:
    """Assert the expected TC2 alert conforms to alert.schema.json."""
    for alert in get_tc2_expected_alerts():
        errors: list[Any] = list(alert_validator.iter_errors(alert))
        assert errors == [], f"Invalid alert {alert['alert_id']}: {errors}"
