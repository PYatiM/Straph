"""Unit tests for Benign Edge-Case Traffic Generator (retries, timeouts, multi-edits)."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft7Validator

from services.detector.worker import DetectorWorker
from services.evaluation.generators.benign_edge_cases import (
    SCENARIO_LONG_DELAY,
    SCENARIO_MULTI_EDIT,
    SCENARIO_PAYMENT_RETRY,
    generate_benign_edge_cases,
)
from services.evaluation.labels import GroundTruthLabel

ROOT_DIR = Path(__file__).resolve().parents[2]
EVENT_SCHEMA_PATH = ROOT_DIR / "contracts" / "event.schema.json"


@pytest.fixture
def event_validator() -> Draft7Validator:
    """Provide Draft7Validator for contracts/event.schema.json."""
    schema: dict[str, Any] = json.loads(EVENT_SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft7Validator.check_schema(schema)
    return Draft7Validator(schema)


def test_generate_benign_edge_cases_zero_violations_and_schema_valid(
    event_validator: Draft7Validator,
) -> None:
    """Verify all generated edge-case events are schema-valid and zero are labeled as attacks."""
    events, labels = generate_benign_edge_cases(seed=42)

    assert len(events) == 11
    assert len(labels) == 11

    for event, label in zip(events, labels, strict=True):
        errors = list(event_validator.iter_errors(event))
        assert errors == [], f"Event failed schema validation: {errors}"
        assert isinstance(label, GroundTruthLabel)
        assert label.event_id == event["event_id"]
        assert label.is_violation is False
        assert label.expected_label == "BENIGN"
        assert label.expected_rule is None
        assert label.missing_ops == []

    # Confirm zero events are labeled as attacks
    assert sum(1 for lbl in labels if lbl.is_violation) == 0


def test_benign_edge_cases_scenarios_content() -> None:
    """Verify the 3 edge-case variations: multi-edit, 500 payment retry, and 2-hour delay."""
    events, labels = generate_benign_edge_cases(seed=42)

    scenario_ids = {lbl.scenario_id for lbl in labels}
    assert scenario_ids == {
        SCENARIO_MULTI_EDIT,
        SCENARIO_PAYMENT_RETRY,
        SCENARIO_LONG_DELAY,
    }

    # 1. Multiple address edits on order edge-1009
    multi_edit_events = [
        ev for ev, lbl in zip(events, labels, strict=True) if lbl.scenario_id == SCENARIO_MULTI_EDIT
    ]
    address_calls = [
        ev
        for ev in multi_edit_events
        if ev["operation"] == {"method": "PUT", "path_template": "/orders/{id}/address"}
    ]
    assert len(address_calls) == 2

    # 2. Client payment retry after 500 error sharing same idempotency_key
    retry_events = [
        ev
        for ev, lbl in zip(events, labels, strict=True)
        if lbl.scenario_id == SCENARIO_PAYMENT_RETRY
    ]
    payment_calls = [
        ev
        for ev in retry_events
        if ev["operation"] == {"method": "POST", "path_template": "/orders/{id}/payment"}
    ]
    assert len(payment_calls) == 2
    assert payment_calls[0]["status_code"] == 500
    assert payment_calls[1]["status_code"] == 200
    assert payment_calls[0]["idempotency_key"] is not None
    assert payment_calls[0]["idempotency_key"] == payment_calls[1]["idempotency_key"]

    # 3. Long delay (2 hours = 7200 seconds) between address setting and payment
    delay_events = [
        ev for ev, lbl in zip(events, labels, strict=True) if lbl.scenario_id == SCENARIO_LONG_DELAY
    ]
    addr_ts = datetime.fromisoformat(delay_events[1]["ts_completed"])
    pay_ts = datetime.fromisoformat(delay_events[2]["ts_received"])
    delta_seconds = (pay_ts - addr_ts).total_seconds()
    assert delta_seconds >= 7199.0


def test_benign_edge_cases_zero_false_positives_in_straph() -> None:
    """Straph DetectorWorker raises zero alerts across all benign edge cases."""
    events, _ = generate_benign_edge_cases(seed=42)
    worker = DetectorWorker()

    alerts = [alert for e in events if (alert := worker.process_event(e)) is not None]
    assert alerts == []


def test_benign_edge_cases_determinism() -> None:
    """Same seed produces identical events and labels."""
    ev_a, lbl_a = generate_benign_edge_cases(seed=88)
    ev_b, lbl_b = generate_benign_edge_cases(seed=88)
    ev_c, _ = generate_benign_edge_cases(seed=89)

    assert ev_a == ev_b
    assert lbl_a == lbl_b
    assert [e["event_id"] for e in ev_a] != [e["event_id"] for e in ev_c]
