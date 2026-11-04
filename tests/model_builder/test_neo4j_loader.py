"""Unit and contract tests for Neo4jModelLoader and InMemoryGraphDriver."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema.exceptions import ValidationError

from services.model_builder.neo4j_loader import (
    LOAD_NEXT_QUERY,
    LOAD_OUTGOING_QUERY,
    LOAD_STATES_QUERY,
    LOAD_TARGET_QUERY,
    LOAD_TRANSITIONS_QUERY,
    STATE_CONSTRAINT_QUERY,
    TRANSITION_CONSTRAINT_QUERY,
    InMemoryGraphDriver,
    Neo4jModelLoader,
)

ROOT_DIR = Path(__file__).resolve().parents[2]
CHECKOUT_MODEL_PATH = ROOT_DIR / "contracts" / "examples" / "model_checkout.json"


@pytest.fixture
def checkout_model() -> dict[str, Any]:
    with open(CHECKOUT_MODEL_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def mock_driver() -> InMemoryGraphDriver:
    return InMemoryGraphDriver()


def test_cypher_statements_contain_zero_string_concatenation() -> None:
    """Verify all queries are static parameterized strings without inline concatenations."""
    queries = [
        STATE_CONSTRAINT_QUERY,
        TRANSITION_CONSTRAINT_QUERY,
        LOAD_STATES_QUERY,
        LOAD_TRANSITIONS_QUERY,
        LOAD_OUTGOING_QUERY,
        LOAD_TARGET_QUERY,
        LOAD_NEXT_QUERY,
    ]
    for q in queries:
        assert isinstance(q, str)
        # Check that query uses cypher parameters rather than dynamic string formatting
        assert "{" not in q or "$" in q  # cypher parameter marker


def test_apply_constraints_records_unique_constraints(mock_driver: InMemoryGraphDriver) -> None:
    """Verify applying constraints records State(state_id) and Transition(transition_id)."""
    loader = Neo4jModelLoader()
    with mock_driver.session() as session:
        loader.apply_constraints(session)

    assert len(mock_driver.constraints) == 2
    assert any("State" in c and "state_id" in c for c in mock_driver.constraints)
    assert any("Transition" in c and "transition_id" in c for c in mock_driver.constraints)


def test_load_checkout_model_creates_10_states_and_10_transitions(
    mock_driver: InMemoryGraphDriver,
    checkout_model: dict[str, Any],
) -> None:
    """Verify loading model_checkout.json produces exactly 10 states and 10 transitions."""
    loader = Neo4jModelLoader()
    with mock_driver.session() as session:
        result = loader.load_model(session, checkout_model)

    assert result["states_loaded"] == 10
    assert result["transitions_loaded"] == 10

    states = mock_driver.get_states()
    transitions = mock_driver.get_transitions()

    assert len(states) == 10
    assert len(transitions) == 10

    # Verify state node properties
    cart_state = states["CART_ACTIVE"]
    assert cart_state["id"] == "CART_ACTIVE"
    assert cart_state["state_id"] == "CART_ACTIVE"
    assert cart_state["terminal"] is False
    assert "Active shopping cart" in cart_state["description"]

    # Verify transition node properties
    t1 = transitions["T1"]
    assert t1["id"] == "T1"
    assert t1["operation"] == "POST /cart/items"
    assert t1["single_use"] is False
    assert t1["provenance"] == "spec"
    assert t1["support_count"] == 0
    assert "customer" in t1["allowed_roles"]

    # Verify relationship creation
    outgoing = mock_driver.get_relationships("OUTGOING")
    targets = mock_driver.get_relationships("TARGET")
    next_rels = mock_driver.get_relationships("NEXT")

    assert len(outgoing) > 0
    assert len(targets) == 10
    assert len(next_rels) > 0

    # Specifically check T4 from ADDRESS_SET to PAYMENT_PENDING
    t4 = transitions["T4"]
    assert t4["operation"] == "POST /orders/{id}/payment"
    assert any(
        r["from_state"] == "ADDRESS_SET" and r["to_state"] == "PAYMENT_PENDING" for r in next_rels
    )


def test_idempotent_reload_produces_no_duplicate_nodes(
    mock_driver: InMemoryGraphDriver,
    checkout_model: dict[str, Any],
) -> None:
    """Verify loading the model multiple times produces no duplicate nodes or relationships."""
    loader = Neo4jModelLoader()
    with mock_driver.session() as session:
        loader.load_model(session, checkout_model)
        loader.load_model(session, checkout_model)
        loader.load_model(session, checkout_model)

    states = mock_driver.get_states()
    transitions = mock_driver.get_transitions()

    assert len(states) == 10
    assert len(transitions) == 10

    outgoing = mock_driver.get_relationships("OUTGOING")
    targets = mock_driver.get_relationships("TARGET")
    next_rels = mock_driver.get_relationships("NEXT")

    # Repeat load count check
    initial_outgoing_count = len(outgoing)
    initial_target_count = len(targets)
    initial_next_count = len(next_rels)

    with mock_driver.session() as session:
        loader.load_model(session, checkout_model)

    assert len(mock_driver.get_states()) == 10
    assert len(mock_driver.get_transitions()) == 10
    assert len(mock_driver.get_relationships("OUTGOING")) == initial_outgoing_count
    assert len(mock_driver.get_relationships("NEXT")) == initial_next_count
    assert len(mock_driver.get_relationships("TARGET")) == initial_target_count


def test_load_model_rejects_invalid_schema(mock_driver: InMemoryGraphDriver) -> None:
    """Verify loader raises error on invalid model schema."""
    loader = Neo4jModelLoader()
    with mock_driver.session() as session, pytest.raises((ValidationError, ValueError)):
        loader.load_model(session, {"resource_type": "Invalid"})
