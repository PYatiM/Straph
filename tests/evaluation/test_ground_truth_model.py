"""Tests for the ground-truth reference checkout model and its generator."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft7Validator

from services.evaluation.ground_truth.checkout_model import (
    DEFAULT_OUTPUT_PATH,
    MODEL_VERSION,
    RESOURCE_TYPE,
    STATES,
    TRANSITIONS,
    build_checkout_model,
    generate_checkout_model,
)
from services.evaluation.target_app.constants import OrderState

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = REPO_ROOT / "contracts" / "model.schema.json"


@pytest.fixture(scope="module")
def model_schema() -> dict[str, Any]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def schema_validator(model_schema: dict[str, Any]) -> Draft7Validator:
    Draft7Validator.check_schema(model_schema)
    return Draft7Validator(model_schema)


@pytest.fixture(scope="module")
def committed_model() -> dict[str, Any]:
    return json.loads(DEFAULT_OUTPUT_PATH.read_text(encoding="utf-8"))


def test_ground_truth_model_strictly_validates_against_schema(
    schema_validator: Draft7Validator,
    committed_model: dict[str, Any],
) -> None:
    errors = list(schema_validator.iter_errors(committed_model))
    assert errors == [], f"Validation errors: {errors}"


def test_build_checkout_model_matches_committed_file(
    committed_model: dict[str, Any],
) -> None:
    generated = build_checkout_model()
    assert generated == committed_model
    assert len(STATES) == 10
    assert len(TRANSITIONS) == 10


def test_generator_writes_valid_json(
    tmp_path: Path,
    schema_validator: Draft7Validator,
) -> None:
    test_out = tmp_path / "model_checkout.json"
    written = generate_checkout_model(test_out)
    assert written == test_out
    loaded = json.loads(test_out.read_text(encoding="utf-8"))
    assert list(schema_validator.iter_errors(loaded)) == []


def test_ground_truth_top_level_fields(committed_model: dict[str, Any]) -> None:
    assert committed_model["model_version"] == MODEL_VERSION
    assert committed_model["resource_type"] == RESOURCE_TYPE
    assert committed_model["initial_state"] == OrderState.CART_ACTIVE.value


def test_ground_truth_states_cardinality_and_order(committed_model: dict[str, Any]) -> None:
    states = committed_model["states"]
    assert len(states) == 10

    expected_states = [
        "CART_ACTIVE",
        "ORDER_CREATED",
        "ADDRESS_SET",
        "PAYMENT_PENDING",
        "PAID",
        "FULFILLMENT_QUEUED",
        "SHIPPED",
        "DELIVERED",
        "CANCELLED",
        "REFUNDED",
    ]
    actual_states = [s["state_id"] for s in states]
    assert actual_states == expected_states

    # Check terminal statuses
    terminal_map = {s["state_id"]: s["terminal"] for s in states}
    assert terminal_map["CANCELLED"] is True
    assert terminal_map["REFUNDED"] is True
    for state_id, terminal in terminal_map.items():
        if state_id not in {"CANCELLED", "REFUNDED"}:
            assert terminal is False, f"State {state_id} should not be terminal"


def test_ground_truth_transitions_cardinality_and_ids(committed_model: dict[str, Any]) -> None:
    transitions = committed_model["transitions"]
    assert len(transitions) == 10

    expected_ids = [f"T{i}" for i in range(1, 11)]
    actual_ids = [t["transition_id"] for t in transitions]
    assert actual_ids == expected_ids


def test_single_use_true_only_on_refund_t10(committed_model: dict[str, Any]) -> None:
    transitions = committed_model["transitions"]
    for t in transitions:
        if t["transition_id"] == "T10":
            assert t["single_use"] is True
            assert t["to_state"] == OrderState.REFUNDED.value
        else:
            assert t["single_use"] is False, f"{t['transition_id']} should have single_use=False"


def test_all_transitions_have_spec_provenance_and_confirmed_status(
    committed_model: dict[str, Any],
) -> None:
    for t in committed_model["transitions"]:
        assert t["provenance"] == "spec"
        assert t["review_status"] == "confirmed"
        assert t["support_count"] == 0


def test_transition_operations_and_roles(committed_model: dict[str, Any]) -> None:
    t_map = {t["transition_id"]: t for t in committed_model["transitions"]}

    # T1: Cart item addition
    assert t_map["T1"]["operation"] == {"method": "POST", "path_template": "/cart/items"}
    assert t_map["T1"]["from_states"] == ["CART_ACTIVE"]
    assert t_map["T1"]["to_state"] == "CART_ACTIVE"
    assert t_map["T1"]["allowed_roles"] == ["customer"]

    # T2: Order creation
    assert t_map["T2"]["operation"] == {"method": "POST", "path_template": "/orders"}
    assert t_map["T2"]["from_states"] == ["CART_ACTIVE"]
    assert t_map["T2"]["to_state"] == "ORDER_CREATED"
    assert t_map["T2"]["allowed_roles"] == ["customer"]

    # T3: Set/Update address
    assert t_map["T3"]["operation"] == {"method": "PUT", "path_template": "/orders/{id}/address"}
    assert t_map["T3"]["from_states"] == ["ORDER_CREATED", "ADDRESS_SET"]
    assert t_map["T3"]["to_state"] == "ADDRESS_SET"
    assert t_map["T3"]["allowed_roles"] == ["customer"]

    # T4: Initiate payment
    assert t_map["T4"]["operation"] == {"method": "POST", "path_template": "/orders/{id}/payment"}
    assert t_map["T4"]["from_states"] == ["ADDRESS_SET"]
    assert t_map["T4"]["to_state"] == "PAYMENT_PENDING"
    assert t_map["T4"]["allowed_roles"] == ["customer"]

    # T5: Payment webhook
    assert t_map["T5"]["operation"] == {"method": "POST", "path_template": "/payments/webhook"}
    assert t_map["T5"]["from_states"] == ["PAYMENT_PENDING"]
    assert t_map["T5"]["to_state"] == "PAID"
    assert t_map["T5"]["allowed_roles"] == ["payment-gateway"]

    # T6: Warehouse fulfill
    assert t_map["T6"]["operation"] == {"method": "POST", "path_template": "/orders/{id}/fulfill"}
    assert t_map["T6"]["from_states"] == ["PAID"]
    assert t_map["T6"]["to_state"] == "FULFILLMENT_QUEUED"
    assert t_map["T6"]["allowed_roles"] == ["warehouse"]

    # T7: Warehouse ship
    assert t_map["T7"]["operation"] == {"method": "POST", "path_template": "/orders/{id}/ship"}
    assert t_map["T7"]["from_states"] == ["FULFILLMENT_QUEUED"]
    assert t_map["T7"]["to_state"] == "SHIPPED"
    assert t_map["T7"]["allowed_roles"] == ["warehouse"]

    # T8: Carrier deliver
    assert t_map["T8"]["operation"] == {"method": "POST", "path_template": "/orders/{id}/deliver"}
    assert t_map["T8"]["from_states"] == ["SHIPPED"]
    assert t_map["T8"]["to_state"] == "DELIVERED"
    assert "carrier" in t_map["T8"]["allowed_roles"]

    # T9: Cancel order
    assert t_map["T9"]["operation"] == {"method": "POST", "path_template": "/orders/{id}/cancel"}
    assert set(t_map["T9"]["from_states"]) == {
        "ORDER_CREATED",
        "ADDRESS_SET",
        "PAYMENT_PENDING",
        "PAID",
    }
    assert t_map["T9"]["to_state"] == "CANCELLED"
    assert "customer" in t_map["T9"]["allowed_roles"]

    # T10: Refund order
    assert t_map["T10"]["operation"] == {"method": "POST", "path_template": "/orders/{id}/refund"}
    assert set(t_map["T10"]["from_states"]) == {"PAID", "DELIVERED"}
    assert t_map["T10"]["to_state"] == "REFUNDED"
    assert t_map["T10"]["allowed_roles"] == ["support"]
    assert t_map["T10"]["single_use"] is True


def test_ground_truth_graph_invariants(committed_model: dict[str, Any]) -> None:
    state_ids = {s["state_id"] for s in committed_model["states"]}
    assert committed_model["initial_state"] in state_ids

    targets_by_op: dict[tuple[str, str], set[str]] = {}
    for t in committed_model["transitions"]:
        # All from_states and to_state exist in defined states
        assert set(t["from_states"]).issubset(state_ids)
        assert t["to_state"] in state_ids

        # Determinism check: each operation has a unique destination state
        op_key = (t["operation"]["method"], t["operation"]["path_template"])
        targets_by_op.setdefault(op_key, set()).add(t["to_state"])

    for op_key, dests in targets_by_op.items():
        assert len(dests) == 1, f"Non-deterministic transitions for {op_key}: {dests}"
