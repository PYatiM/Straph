"""Unit tests for Attack Generator 2: Post-Shipping Address Edit (address_after_shipping)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft7Validator

from services.detector.worker import DetectorWorker
from services.evaluation.generators.attack_backward import (
    SCENARIO_ID,
    generate_post_ship_edit_attack,
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


def test_generate_post_ship_edit_attack_schema_and_labels(
    event_validator: Draft7Validator,
) -> None:
    """Verify generator produces 8 schema-valid events and exact GroundTruthLabel records."""
    events, labels = generate_post_ship_edit_attack(order_id="1003", seed=42)

    assert len(events) == 8
    assert len(labels) == 8

    for event, label in zip(events, labels, strict=True):
        errors = list(event_validator.iter_errors(event))
        assert errors == [], f"Event failed schema validation: {errors}"
        assert isinstance(label, GroundTruthLabel)
        assert label.event_id == event["event_id"]
        assert label.scenario_id == SCENARIO_ID
        assert label.seed == 42

    # Calls 1-7 are benign and advance the order to SHIPPED
    for idx in range(7):
        assert labels[idx].is_violation is False
        assert labels[idx].expected_label == "BENIGN"
        assert labels[idx].expected_rule is None
        assert labels[idx].missing_ops == []

    assert events[6]["operation"] == {"method": "POST", "path_template": "/orders/{id}/ship"}
    assert events[6]["status_code"] == 200

    # Call 8: PUT /orders/{id}/address -> 200 (OUT_OF_ORDER, R3)
    assert events[7]["operation"] == {"method": "PUT", "path_template": "/orders/{id}/address"}
    assert events[7]["status_code"] == 200
    assert events[7]["resource_type"] == "Order"
    assert events[7]["resource_id"] == "1003"

    assert labels[7].is_violation is True
    assert labels[7].expected_label == "OUT_OF_ORDER"
    assert labels[7].expected_rule == "R3"
    assert labels[7].missing_ops == []


def test_generate_post_ship_edit_determinism_and_seed_variation() -> None:
    """Same seed produces identical records; different seed varies UUIDs."""
    ev_a, lbl_a = generate_post_ship_edit_attack(order_id="order-1003", seed=7)
    ev_b, lbl_b = generate_post_ship_edit_attack(order_id="order-1003", seed=7)
    ev_c, _ = generate_post_ship_edit_attack(order_id="order-1003", seed=8)

    assert ev_a == ev_b
    assert lbl_a == lbl_b
    assert [e["event_id"] for e in ev_a] != [e["event_id"] for e in ev_c]


def test_generate_post_ship_edit_invalid_order_id() -> None:
    """Empty order_id raises ValueError."""
    with pytest.raises(ValueError, match="order_id must be a non-empty string"):
        generate_post_ship_edit_attack(order_id="")


def test_generate_post_ship_edit_detector_alignment() -> None:
    """Straph DetectorWorker raises exact R3 OUT_OF_ORDER alert on call #8."""
    events, labels = generate_post_ship_edit_attack(order_id="1003", seed=42)
    worker = DetectorWorker()

    alerts = [worker.process_event(e) for e in events]
    for idx in range(7):
        assert alerts[idx] is None

    assert alerts[7] is not None
    assert alerts[7]["event_id"] == labels[7].event_id
    assert alerts[7]["label"] == labels[7].expected_label
    assert alerts[7]["rule"] == labels[7].expected_rule
    assert alerts[7]["missing"] == labels[7].missing_ops
