"""Tests for TC7 split-token skip fixtures and schema compliance."""

import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from tests.detector.fixtures.tc7_split_token_skip import (
    get_tc7_events,
    get_tc7_expected_alerts,
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


def test_tc7_sequence_structure() -> None:
    """Assert the 3-call sequence for order 1007 with expected statuses."""
    events: list[dict[str, Any]] = get_tc7_events()
    specs: list[tuple[str, str, int]] = [
        ("POST", "/orders", 201),
        ("PUT", "/orders/1007/address", 200),
        ("POST", "/orders/1007/fulfill", 200),
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


def test_tc7_token_and_session_rotate_but_identity_and_object_do_not() -> None:
    """Assert rotation changes token/session only; identity and object stay constant."""
    events: list[dict[str, Any]] = get_tc7_events()
    first, second, third = events
    assert first["token_id"] == second["token_id"]
    assert first["session_id"] == second["session_id"]
    assert third["token_id"] != second["token_id"]
    assert third["session_id"] != second["session_id"]
    assert {e["subject"]["sub"] for e in events} == {"user-a"}
    assert {(e["resource_type"], e["resource_id"]) for e in events} == {("Order", "1007")}


@pytest.mark.parametrize("event", get_tc7_events())
def test_each_tc7_event_validates_against_event_schema(
    event_validator: Draft202012Validator,
    event: dict[str, Any],
) -> None:
    """Assert every TC7 event conforms to event.schema.json."""
    errors: list[Any] = list(event_validator.iter_errors(event))
    assert errors == [], f"Invalid event {event['event_id']}: {errors}"


def test_tc7_expected_alert_flags_skip_on_rotated_token() -> None:
    """Assert the R2 alert fires on the rotated-token event with payment+webhook missing."""
    alerts: list[dict[str, Any]] = get_tc7_expected_alerts()
    assert len(alerts) == 1
    alert: dict[str, Any] = alerts[0]
    final: dict[str, Any] = get_tc7_events()[-1]
    assert alert["label"] == "SKIPPED"
    assert alert["rule"] == "R2"
    assert alert["severity"] == "accepted"
    assert alert["missing"] == ["POST /orders/{id}/payment", "POST /payments/webhook"]
    assert alert["event_id"] == final["event_id"]
    assert alert["subject"]["token_id"] == final["token_id"]
    assert alert["subject"]["session_id"] == final["session_id"]
    assert alert["subject"]["token_id"] != get_tc7_events()[0]["token_id"]


def test_tc7_expected_alert_validates_against_alert_schema(
    alert_validator: Draft202012Validator,
) -> None:
    """Assert the expected TC7 alert conforms to alert.schema.json."""
    for alert in get_tc7_expected_alerts():
        errors: list[Any] = list(alert_validator.iter_errors(alert))
        assert errors == [], f"Invalid alert {alert['alert_id']}: {errors}"
