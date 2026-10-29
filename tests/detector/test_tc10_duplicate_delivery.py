"""Tests for TC10 duplicate-delivery fixtures and schema compliance."""

import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from tests.detector.fixtures.tc10_duplicate_delivery import (
    get_tc10_events,
    get_tc10_expected_alerts,
)

ROOT: Path = Path(__file__).resolve().parents[2]
EVENT_SCHEMA_PATH: Path = ROOT / "contracts" / "event.schema.json"


@pytest.fixture
def event_validator() -> Draft202012Validator:
    """Provide a validator for event.schema.json."""
    schema: dict[str, Any] = json.loads(EVENT_SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def test_tc10_sequence_structure() -> None:
    """Assert 4 events: three legal calls to ADDRESS_SET plus one re-delivery."""
    events: list[dict[str, Any]] = get_tc10_events()
    specs: list[tuple[str, str, int]] = [
        ("POST", "/cart/items", 200),
        ("POST", "/orders", 201),
        ("PUT", "/orders/1010/address", 200),
        ("PUT", "/orders/1010/address", 200),
    ]
    assert len(events) == len(specs)
    for event, (method, path, status) in zip(events, specs, strict=True):
        assert (event["method"], event["path_template"], event["status"]) == (
            method,
            path,
            status,
        )


def test_tc10_last_event_is_verbatim_redelivery_of_third() -> None:
    """Assert the duplicate has identical event_id, timestamps and payload, as a copy."""
    events: list[dict[str, Any]] = get_tc10_events()
    third, duplicate = events[2], events[3]
    assert duplicate == third
    assert duplicate is not third
    assert duplicate["event_id"] == third["event_id"]
    assert duplicate["ts_received"] == third["ts_received"]
    assert duplicate["ts_completed"] == third["ts_completed"]


def test_tc10_only_the_redelivery_repeats_an_event_id() -> None:
    """Assert only the re-delivery repeats an event_id (4 events, 3 distinct ids)."""
    ids: list[str] = [e["event_id"] for e in get_tc10_events()]
    assert len(ids) == 4
    assert len(set(ids)) == 3
    assert ids[2] == ids[3]


def test_tc10_expected_alerts_empty() -> None:
    """Assert duplicate delivery produces zero alerts."""
    assert get_tc10_expected_alerts() == []


@pytest.mark.parametrize("event", get_tc10_events())
def test_each_tc10_event_validates_against_event_schema(
    event_validator: Draft202012Validator,
    event: dict[str, Any],
) -> None:
    """Assert every TC10 event conforms to event.schema.json."""
    errors: list[Any] = list(event_validator.iter_errors(event))
    assert errors == [], f"Invalid event {event['event_id']}: {errors}"
