"""Unit tests for Attack Generator 1: Skip Payment (skip_payment)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft7Validator

from services.detector.worker import DetectorWorker
from services.evaluation.generators.attack_skip import (
    MISSING_PAYMENT_OPS,
    SCENARIO_ID,
    generate_skip_payment_attack,
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


def test_generate_skip_payment_attack_schema_and_labels(
    event_validator: Draft7Validator,
) -> None:
    """Verify generator produces 3 schema-valid events and exact GroundTruthLabel records."""
    events, labels = generate_skip_payment_attack(order_id="1002", seed=42)

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
        assert event["resource_id"] == "1002"

    # Sequence step checks
    assert events[0]["operation"] == {"method": "POST", "path_template": "/orders"}
    assert events[0]["status_code"] == 201
    assert labels[0].is_violation is False
    assert labels[0].expected_label == "BENIGN"
    assert labels[0].expected_rule is None
    assert labels[0].missing_ops == []

    assert events[1]["operation"] == {"method": "PUT", "path_template": "/orders/{id}/address"}
    assert events[1]["status_code"] == 200
    assert labels[1].is_violation is False
    assert labels[1].expected_label == "BENIGN"
    assert labels[1].expected_rule is None
    assert labels[1].missing_ops == []

    assert events[2]["operation"] == {"method": "POST", "path_template": "/orders/{id}/fulfill"}
    assert events[2]["status_code"] == 200
    assert labels[2].is_violation is True
    assert labels[2].expected_label == "SKIPPED"
    assert labels[2].expected_rule == "R2"
    assert labels[2].missing_ops == MISSING_PAYMENT_OPS


def test_generate_skip_payment_determinism_and_seed_variation() -> None:
    """Same seed produces identical records; different seed varies UUIDs."""
    ev_a, lbl_a = generate_skip_payment_attack(order_id="order-99", seed=101)
    ev_b, lbl_b = generate_skip_payment_attack(order_id="order-99", seed=101)
    ev_c, _ = generate_skip_payment_attack(order_id="order-99", seed=202)

    assert ev_a == ev_b
    assert lbl_a == lbl_b
    assert [e["event_id"] for e in ev_a] != [e["event_id"] for e in ev_c]


def test_generate_skip_payment_invalid_order_id() -> None:
    """Empty order_id raises ValueError."""
    with pytest.raises(ValueError, match="order_id must be a non-empty string"):
        generate_skip_payment_attack(order_id="   ")


def test_generate_skip_payment_detector_alignment() -> None:
    """Straph DetectorWorker raises exact R2 SKIPPED alert matching ground truth."""
    events, labels = generate_skip_payment_attack(order_id="1002", seed=42)
    worker = DetectorWorker()

    alerts = [worker.process_event(e) for e in events]
    assert alerts[0] is None
    assert alerts[1] is None
    assert alerts[2] is not None
    assert alerts[2]["event_id"] == labels[2].event_id
    assert alerts[2]["label"] == labels[2].expected_label
    assert alerts[2]["rule"] == labels[2].expected_rule
    assert alerts[2]["missing"] == labels[2].missing_ops
