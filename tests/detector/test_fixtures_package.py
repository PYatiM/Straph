"""Consolidated contract-compliance tests for the TC1-TC10 fixture package."""

import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from tests.detector import fixtures
from tests.detector.fixtures import ALL_FIXTURES

ROOT: Path = Path(__file__).resolve().parents[2]
CONTRACTS_DIR: Path = ROOT / "contracts"

EXPECTED_ALERT_COUNTS: dict[str, int] = {
    "tc1": 0,
    "tc2": 1,
    "tc3": 1,
    "tc4": 1,
    "tc5": 1,
    "tc6": 1,
    "tc7": 1,
    "tc8": 0,
    "tc9": 0,
    "tc10": 0,
}


def _validator(name: str) -> Draft202012Validator:
    schema: dict[str, Any] = json.loads((CONTRACTS_DIR / name).read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


EVENT_VALIDATOR: Draft202012Validator = _validator("event.schema.json")
ALERT_VALIDATOR: Draft202012Validator = _validator("alert.schema.json")


def test_all_ten_fixtures_registered() -> None:
    """Assert TC1..TC10 are all registered in ALL_FIXTURES."""
    assert list(ALL_FIXTURES) == [f"tc{i}" for i in range(1, 11)]


def test_all_fixture_functions_exported() -> None:
    """Assert every get_tcN_* function is importable from the package and in __all__."""
    for i in range(1, 11):
        for suffix in ("events", "expected_alerts"):
            name: str = f"get_tc{i}_{suffix}"
            assert name in fixtures.__all__
            assert callable(getattr(fixtures, name))


@pytest.mark.parametrize("tc_id", list(ALL_FIXTURES))
def test_events_comply_with_event_contract(tc_id: str) -> None:
    """Assert every event of every fixture validates against event.schema.json."""
    get_events, _ = ALL_FIXTURES[tc_id]
    events: list[dict[str, Any]] = get_events()
    assert events
    for event in events:
        errors: list[Any] = list(EVENT_VALIDATOR.iter_errors(event))
        assert errors == [], f"{tc_id} event {event['event_id']}: {errors}"


@pytest.mark.parametrize("tc_id", list(ALL_FIXTURES))
def test_expected_alerts_comply_with_alert_contract(tc_id: str) -> None:
    """Assert every expected alert validates against alert.schema.json and counts match."""
    _, get_alerts = ALL_FIXTURES[tc_id]
    alerts: list[dict[str, Any]] = get_alerts()
    assert len(alerts) == EXPECTED_ALERT_COUNTS[tc_id]
    for alert in alerts:
        errors: list[Any] = list(ALERT_VALIDATOR.iter_errors(alert))
        assert errors == [], f"{tc_id} alert {alert['alert_id']}: {errors}"


@pytest.mark.parametrize("tc_id", list(ALL_FIXTURES))
def test_alerts_reference_existing_events(tc_id: str) -> None:
    """Assert each expected alert points at an event_id present in its fixture."""
    get_events, get_alerts = ALL_FIXTURES[tc_id]
    event_ids: set[str] = {e["event_id"] for e in get_events()}
    for alert in get_alerts():
        assert alert["event_id"] in event_ids


def test_event_ids_unique_across_fixtures_except_tc10_redelivery() -> None:
    """Assert event_ids never collide across fixtures; only TC10 repeats one by design."""
    seen: list[str] = []
    for tc_id, (get_events, _) in ALL_FIXTURES.items():
        ids: list[str] = [e["event_id"] for e in get_events()]
        if tc_id == "tc10":
            ids = list(dict.fromkeys(ids))
        else:
            assert len(ids) == len(set(ids)), f"{tc_id} has duplicate event_ids"
        seen.extend(ids)
    assert len(seen) == len(set(seen))
