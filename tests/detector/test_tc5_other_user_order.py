"""Tests for TC5 other-user-order (BOLA) fixtures and schema compliance."""

import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from tests.detector.fixtures.tc5_other_user_order import (
    get_tc5_events,
    get_tc5_expected_alerts,
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


def test_tc5_sequence_structure() -> None:
    """Assert owner creates order 1005 and a different customer cancels it."""
    events: list[dict[str, Any]] = get_tc5_events()
    assert len(events) == 2
    create, cancel = events
    assert (create["identity"], create["role"]) == ("user-alpha", "customer")
    assert (create["method"], create["path_template"], create["status"]) == (
        "POST",
        "/orders",
        201,
    )
    assert (cancel["identity"], cancel["role"]) == ("user-bravo", "customer")
    assert (cancel["method"], cancel["path_template"], cancel["status"]) == (
        "POST",
        "/orders/1005/cancel",
        200,
    )
    assert create["resource_id"] == cancel["resource_id"] == "1005"
    assert create["identity"] != cancel["identity"]


@pytest.mark.parametrize("event", get_tc5_events())
def test_each_tc5_event_validates_against_event_schema(
    event_validator: Draft202012Validator,
    event: dict[str, Any],
) -> None:
    """Assert every TC5 event conforms to event.schema.json."""
    errors: list[Any] = list(event_validator.iter_errors(event))
    assert errors == [], f"Invalid event {event['event_id']}: {errors}"


def test_tc5_expected_alert_content() -> None:
    """Assert the single alert is an accepted R0 IDENTITY_MISMATCH with CANCELLED secondary."""
    alerts: list[dict[str, Any]] = get_tc5_expected_alerts()
    assert len(alerts) == 1
    alert: dict[str, Any] = alerts[0]
    assert alert["label"] == "IDENTITY_MISMATCH"
    assert alert["rule"] == "R0"
    assert alert["secondary_label"] == "CANCELLED"
    assert alert["subject"] == {"sub": "user-bravo"}
    assert alert["severity"] == "accepted"
    assert alert["event_id"] == get_tc5_events()[-1]["event_id"]


def test_tc5_expected_alert_validates_against_alert_schema(
    alert_validator: Draft202012Validator,
) -> None:
    """Assert the expected TC5 alert conforms to alert.schema.json."""
    for alert in get_tc5_expected_alerts():
        errors: list[Any] = list(alert_validator.iter_errors(alert))
        assert errors == [], f"Invalid alert {alert['alert_id']}: {errors}"
