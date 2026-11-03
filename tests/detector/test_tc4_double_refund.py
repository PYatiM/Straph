"""Tests for TC4 double-refund fixtures and schema compliance."""

import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from tests.detector.fixtures.tc4_double_refund import (
    get_tc4_events,
    get_tc4_expected_alerts,
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


def test_tc4_sequence_structure() -> None:
    """Assert TC4 reaches DELIVERED, then refunds twice with identical calls."""
    events: list[dict[str, Any]] = get_tc4_events()
    assert len(events) == 10
    assert len({e["event_id"] for e in events}) == len(events)
    assert events[7]["operation"]["path_template"] == "/orders/1004/deliver"
    first, second = events[8], events[9]
    for refund in (first, second):
        assert refund["operation"]["method"] == "POST"
        assert refund["operation"]["path_template"] == "/orders/1004/refund"
        assert refund["subject"]["role"] == "support"
        assert refund["status_code"] == 200
    assert first["event_id"] != second["event_id"]


@pytest.mark.parametrize("event", get_tc4_events())
def test_each_tc4_event_validates_against_event_schema(
    event_validator: Draft202012Validator,
    event: dict[str, Any],
) -> None:
    """Assert every TC4 event conforms to event.schema.json."""
    errors: list[Any] = list(event_validator.iter_errors(event))
    assert errors == [], f"Invalid event {event['event_id']}: {errors}"


def test_tc4_expected_alert_content() -> None:
    """Assert the single alert is an accepted R1 REPLAYED on the second refund."""
    alerts: list[dict[str, Any]] = get_tc4_expected_alerts()
    assert len(alerts) == 1
    alert: dict[str, Any] = alerts[0]
    assert alert["label"] == "REPLAYED"
    assert alert["rule"] == "R1"
    assert alert["severity"] == "accepted"
    assert alert["observed"]["operation"] == "POST /orders/{id}/refund"
    assert alert["observed"]["state"] == "REFUNDED"
    assert alert["event_id"] == get_tc4_events()[-1]["event_id"]


def test_tc4_expected_alert_validates_against_alert_schema(
    alert_validator: Draft202012Validator,
) -> None:
    """Assert the expected TC4 alert conforms to alert.schema.json."""
    for alert in get_tc4_expected_alerts():
        errors: list[Any] = list(alert_validator.iter_errors(alert))
        assert errors == [], f"Invalid alert {alert['alert_id']}: {errors}"
