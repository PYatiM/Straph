"""Unit tests for Attack Generator 3: Double Refund Replay (double_refund)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft7Validator

from services.detector.worker import DetectorWorker
from services.evaluation.generators.attack_replay import (
    SCENARIO_ID,
    generate_double_refund_attack,
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


def test_generate_double_refund_attack_schema_and_labels(
    event_validator: Draft7Validator,
) -> None:
    """Verify generator produces 10 schema-valid events and exact GroundTruthLabel records."""
    events, labels = generate_double_refund_attack(order_id="1004", seed=42)

    assert len(events) == 10
    assert len(labels) == 10

    for event, label in zip(events, labels, strict=True):
        errors = list(event_validator.iter_errors(event))
        assert errors == [], f"Event failed schema validation: {errors}"
        assert isinstance(label, GroundTruthLabel)
        assert label.event_id == event["event_id"]
        assert label.scenario_id == SCENARIO_ID
        assert label.seed == 42

    # First 9 events (including first refund) are benign
    for idx in range(9):
        assert labels[idx].is_violation is False
        assert labels[idx].expected_label == "BENIGN"
        assert labels[idx].expected_rule is None
        assert labels[idx].missing_ops == []

    # Event #8 reaches DELIVERED
    assert events[7]["operation"] == {"method": "POST", "path_template": "/orders/{id}/deliver"}
    assert events[7]["status_code"] == 200

    # Event #9 is the first (legal) refund by support
    assert events[8]["operation"] == {"method": "POST", "path_template": "/orders/{id}/refund"}
    assert events[8]["subject"]["role"] == "support"
    assert events[8]["status_code"] == 200

    # Event #10 (final sequence step) is the replayed one-shot refund
    assert events[-1]["operation"] == {"method": "POST", "path_template": "/orders/{id}/refund"}
    assert events[-1]["subject"]["role"] == "support"
    assert events[-1]["status_code"] == 200
    assert events[-1]["resource_type"] == "Order"
    assert events[-1]["resource_id"] == "1004"

    assert labels[-1].is_violation is True
    assert labels[-1].expected_label == "REPLAYED"
    assert labels[-1].expected_rule == "R1"
    assert labels[-1].missing_ops == []


def test_generate_double_refund_determinism_and_seed_variation() -> None:
    """Same seed produces identical records; different seed varies UUIDs."""
    ev_a, lbl_a = generate_double_refund_attack(order_id="order-1004", seed=15)
    ev_b, lbl_b = generate_double_refund_attack(order_id="order-1004", seed=15)
    ev_c, _ = generate_double_refund_attack(order_id="order-1004", seed=16)

    assert ev_a == ev_b
    assert lbl_a == lbl_b
    assert [e["event_id"] for e in ev_a] != [e["event_id"] for e in ev_c]


def test_generate_double_refund_invalid_order_id() -> None:
    """Empty order_id raises ValueError."""
    with pytest.raises(ValueError, match="order_id must be a non-empty string"):
        generate_double_refund_attack(order_id="  ")


def test_generate_double_refund_detector_alignment() -> None:
    """Straph DetectorWorker raises exact R1 REPLAYED alert on the second refund."""
    events, labels = generate_double_refund_attack(order_id="1004", seed=42)
    worker = DetectorWorker()

    alerts = [worker.process_event(e) for e in events]
    for idx in range(9):
        assert alerts[idx] is None

    assert alerts[-1] is not None
    assert alerts[-1]["event_id"] == labels[-1].event_id
    assert alerts[-1]["label"] == labels[-1].expected_label
    assert alerts[-1]["rule"] == labels[-1].expected_rule
    assert alerts[-1]["missing"] == labels[-1].missing_ops
