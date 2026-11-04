"""Unit tests for Attack Generator 6: Split-Token Payment Skip across rotated sessions."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft7Validator

from services.detector.worker import DetectorWorker
from services.evaluation.generators.attack_split_token import (
    MISSING_PAYMENT_OPS,
    SCENARIO_ID,
    generate_split_token_attack,
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


def test_generate_split_token_attack_schema_and_rotated_identifiers(
    event_validator: Draft7Validator,
) -> None:
    """Verify schema validity, rotated token_id/session_id, and R2 SKIPPED label."""
    events, labels = generate_split_token_attack(order_id="1007", seed=42)

    assert len(events) == 3
    assert len(labels) == 3

    for event, label in zip(events, labels, strict=True):
        errors = list(event_validator.iter_errors(event))
        assert errors == [], f"Event failed schema validation: {errors}"
        assert isinstance(label, GroundTruthLabel)
        assert label.event_id == event["event_id"]
        assert label.scenario_id == SCENARIO_ID
        assert label.seed == 42
        assert event["resource_type"] == "Order"
        assert event["resource_id"] == "1007"

    # Same user subject across all 3 events
    assert events[0]["subject"]["sub"] == events[1]["subject"]["sub"]
    assert events[1]["subject"]["sub"] == events[2]["subject"]["sub"]

    # Events 0 and 1 share Token 1 and Session 1
    assert events[0]["token_id"] == events[1]["token_id"]
    assert events[0]["session_id"] == events[1]["session_id"]

    # Event 2 (attack call) uses rotated Token 2 and Session 2
    assert events[2]["token_id"] != events[0]["token_id"]
    assert events[2]["session_id"] != events[0]["session_id"]

    # Labels check
    assert labels[0].is_violation is False
    assert labels[0].expected_label == "BENIGN"
    assert labels[1].is_violation is False
    assert labels[1].expected_label == "BENIGN"

    assert events[2]["operation"] == {"method": "POST", "path_template": "/orders/{id}/fulfill"}
    assert events[2]["status_code"] == 200
    assert labels[2].is_violation is True
    assert labels[2].expected_label == "SKIPPED"
    assert labels[2].expected_rule == "R2"
    assert labels[2].missing_ops == MISSING_PAYMENT_OPS


def test_generate_split_token_per_object_instance_tracking() -> None:
    """Assert per-object instance tracking in Straph marks rotated-token call as SKIPPED (R2)."""
    events, labels = generate_split_token_attack(order_id="1007", seed=42)
    worker = DetectorWorker()

    alerts = [worker.process_event(e) for e in events]
    assert alerts[0] is None
    assert alerts[1] is None
    assert alerts[2] is not None
    assert alerts[2]["event_id"] == labels[2].event_id
    assert alerts[2]["label"] == "SKIPPED"
    assert alerts[2]["rule"] == "R2"
    assert alerts[2]["missing"] == MISSING_PAYMENT_OPS


def test_generate_split_token_determinism_and_seed_variation() -> None:
    """Same seed produces identical records; different seed varies UUIDs."""
    ev_a, lbl_a = generate_split_token_attack(order_id="1007", seed=77)
    ev_b, lbl_b = generate_split_token_attack(order_id="1007", seed=77)
    ev_c, _ = generate_split_token_attack(order_id="1007", seed=78)

    assert ev_a == ev_b
    assert lbl_a == lbl_b
    assert [e["event_id"] for e in ev_a] != [e["event_id"] for e in ev_c]


def test_generate_split_token_invalid_order_id() -> None:
    """Empty order_id raises ValueError."""
    with pytest.raises(ValueError, match="order_id must be a non-empty string"):
        generate_split_token_attack(order_id="")
