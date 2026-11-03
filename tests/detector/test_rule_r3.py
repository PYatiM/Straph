"""Tests for Rule R3 (Out-of-Order Transition) and R2/R3 Partition Proposition."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from services.detector.classifier import classify
from services.detector.store import InstanceRecord
from services.detector.tables import ModelTables, load_tables
from tests.detector.fixtures.tc3_address_after_shipping import get_tc3_events

ROOT = Path(__file__).resolve().parents[2]
CHECKOUT_MODEL_PATH = ROOT / "contracts" / "examples" / "model_checkout.json"


@pytest.fixture
def tables() -> ModelTables:
    with open(CHECKOUT_MODEL_PATH, "r", encoding="utf-8") as f:
        model_dict: dict[str, Any] = json.load(f)
    return load_tables(model_dict)


def test_tc3_address_after_shipping_triggers_r3(tables: ModelTables) -> None:
    """Verify TC3 address edit after shipping triggers Rule R3 OUT_OF_ORDER."""
    order = InstanceRecord(
        resource_type="Order",
        resource_id="1003",
        current_state="START",
        owner_subject="customer-1003",
    )
    cart = InstanceRecord(
        resource_type="Cart",
        resource_id="cart-1003",
        current_state="START",
        owner_subject="customer-1003",
    )

    events = get_tc3_events()
    for event in events[:7]:
        inst = order if event.get("resource_type") == "Order" else cart
        res = classify(inst, event, tables)
        assert res is None

    assert order.current_state == "SHIPPED"

    # Event 8: PUT address after shipping
    res = classify(order, events[7], tables)
    assert res is not None
    assert res.is_violation is True
    assert res.label == "OUT_OF_ORDER"
    assert res.rule == "R3"
    assert res.severity == "accepted"
    assert res.missing == []
    assert res.expected_path == []


def test_partition_proposition_r2_r3(tables: ModelTables) -> None:
    """Property test: For any illegal, modeled, non-replay operation, exactly one of R2 or R3 fires."""
    with open(CHECKOUT_MODEL_PATH, "r", encoding="utf-8") as f:
        model_dict: dict[str, Any] = json.load(f)

    all_states = [s["state_id"] for s in model_dict.get("states", [])]
    transitions = model_dict.get("transitions", [])

    tested_combinations = 0

    for state in all_states:
        for t in transitions:
            op = t["operation"]
            method = op["method"]
            path = op["path_template"]
            canonical_op = f"{method} {path}"

            # Check if illegal from this state and not a one-shot replay
            valid_sources = tables.source_states.get(canonical_op, set())
            if state in valid_sources:
                continue
            if canonical_op in tables.one_shot_ops:
                continue

            instance = InstanceRecord(
                resource_type="Order",
                resource_id="prop_test",
                current_state=state,
                owner_subject="customer",
            )
            event = {
                "event_id": "00000000-0000-4000-8000-000000000001",
                "operation": {"method": method, "path_template": path},
                "status_code": 200,
                "subject": {"sub": "customer", "role": "customer"},
            }

            result = classify(instance, event, tables)
            assert result is not None
            assert result.is_violation is True

            is_r2 = (result.rule == "R2") and (result.label == "SKIPPED")
            is_r3 = (result.rule == "R3") and (result.label == "OUT_OF_ORDER")

            # XOR check: strictly one of R2 or R3 must fire
            assert is_r2 ^ is_r3, (
                f"Partition proposition violated at state={state}, op={canonical_op}: "
                f"rule={result.rule}, label={result.label}"
            )
            tested_combinations += 1

    assert tested_combinations > 0, "No combinations tested in partition test"
