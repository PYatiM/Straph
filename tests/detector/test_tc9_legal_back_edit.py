"""Tests for TC9 legal back-edit fixtures and schema compliance."""

import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from tests.detector.fixtures.tc9_legal_back_edit import (
    get_tc9_events,
    get_tc9_expected_alerts,
)

ROOT: Path = Path(__file__).resolve().parents[2]
EVENT_SCHEMA_PATH: Path = ROOT / "contracts" / "event.schema.json"


@pytest.fixture
def event_validator() -> Draft202012Validator:
    """Provide a validator for event.schema.json."""
    schema: dict[str, Any] = json.loads(EVENT_SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def test_tc9_sequence_structure() -> None:
    """Assert create followed by two address edits, all successful."""
    events: list[dict[str, Any]] = get_tc9_events()
    specs: list[tuple[str, str, int]] = [
        ("POST", "/orders", 201),
        ("PUT", "/orders/1009/address", 200),
        ("PUT", "/orders/1009/address", 200),
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
        assert event["resource_type"] == "Order"
        assert event["resource_id"] == "1009"
    assert len({e["event_id"] for e in events}) == len(events)


def test_tc9_address_self_loop_is_repeated_operation() -> None:
    """Assert the last two events are the same operation (self-loop) by the same caller."""
    second, third = get_tc9_events()[1:]
    assert (second["operation"]["method"], second["operation"]["path_template"]) == (
        third["operation"]["method"],
        third["operation"]["path_template"],
    )
    assert second["subject"]["sub"] == third["subject"]["sub"]
    assert second["event_id"] != third["event_id"]


def test_tc9_expected_alerts_empty() -> None:
    """Assert legal self-loops produce zero alerts."""
    assert get_tc9_expected_alerts() == []


@pytest.mark.parametrize("event", get_tc9_events())
def test_each_tc9_event_validates_against_event_schema(
    event_validator: Draft202012Validator,
    event: dict[str, Any],
) -> None:
    """Assert every TC9 event conforms to event.schema.json."""
    errors: list[Any] = list(event_validator.iter_errors(event))
    assert errors == [], f"Invalid event {event['event_id']}: {errors}"
