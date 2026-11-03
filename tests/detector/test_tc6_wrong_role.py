"""Tests for TC6 wrong-role (function-level authorization) fixtures and schema compliance."""

import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from tests.detector.fixtures.tc6_wrong_role import (
    get_tc6_events,
    get_tc6_expected_alerts,
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


def test_tc6_sequence_structure() -> None:
    """Assert order 1006 reaches PAID, then a customer calls warehouse-only fulfill."""
    events: list[dict[str, Any]] = get_tc6_events()
    assert len(events) == 6
    assert events[4]["operation"]["path_template"] == "/payments/webhook"
    final: dict[str, Any] = events[-1]
    assert final["operation"]["method"] == "POST"
    assert final["operation"]["path_template"] == "/orders/1006/fulfill"
    assert final["subject"]["role"] == "customer"
    assert final["subject"]["role"] != "warehouse"
    assert final["status_code"] == 200
    assert len({e["event_id"] for e in events}) == len(events)


@pytest.mark.parametrize("event", get_tc6_events())
def test_each_tc6_event_validates_against_event_schema(
    event_validator: Draft202012Validator,
    event: dict[str, Any],
) -> None:
    """Assert every TC6 event conforms to event.schema.json."""
    errors: list[Any] = list(event_validator.iter_errors(event))
    assert errors == [], f"Invalid event {event['event_id']}: {errors}"


def test_tc6_expected_alert_content() -> None:
    """Assert the single alert is an accepted R0 IDENTITY_MISMATCH on the fulfill event."""
    alerts: list[dict[str, Any]] = get_tc6_expected_alerts()
    assert len(alerts) == 1
    alert: dict[str, Any] = alerts[0]
    assert alert["label"] == "IDENTITY_MISMATCH"
    assert alert["rule"] == "R0"
    assert alert["severity"] == "accepted"
    assert alert["subject"]["role"] == "customer"
    assert alert["event_id"] == get_tc6_events()[-1]["event_id"]


def test_tc6_expected_alert_validates_against_alert_schema(
    alert_validator: Draft202012Validator,
) -> None:
    """Assert the expected TC6 alert conforms to alert.schema.json."""
    for alert in get_tc6_expected_alerts():
        errors: list[Any] = list(alert_validator.iter_errors(alert))
        assert errors == [], f"Invalid alert {alert['alert_id']}: {errors}"
