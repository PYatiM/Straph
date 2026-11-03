"""Unit and contract tests for graph queries on reference workflow models."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from services.model_builder.graph_queries import (
    CYPHER_IS_REACHABLE,
    CYPHER_LEGAL_TRANSITIONS,
    CYPHER_SHORTEST_PATH,
    CYPHER_SOURCE_STATES,
    get_legal_transitions,
    get_shortest_path,
    get_source_states,
    is_state_reachable,
)
from services.model_builder.neo4j_loader import InMemoryGraphDriver, Neo4jModelLoader

ROOT_DIR = Path(__file__).resolve().parents[2]
CHECKOUT_MODEL_PATH = ROOT_DIR / "contracts" / "examples" / "model_checkout.json"


@pytest.fixture
def populated_driver() -> InMemoryGraphDriver:
    with open(CHECKOUT_MODEL_PATH, "r", encoding="utf-8") as f:
        checkout_model: dict[str, Any] = json.load(f)

    driver = InMemoryGraphDriver()
    loader = Neo4jModelLoader()
    with driver.session() as session:
        loader.load_model(session, checkout_model)
    return driver


def test_cypher_queries_are_parameterized() -> None:
    """Ensure Cypher query constants use parameter placeholders without concatenations."""
    queries = [
        CYPHER_LEGAL_TRANSITIONS,
        CYPHER_SOURCE_STATES,
        CYPHER_SHORTEST_PATH,
        CYPHER_IS_REACHABLE,
    ]
    for q in queries:
        assert isinstance(q, str)
        assert "$" in q


def test_shortest_path_address_set_to_paid(populated_driver: InMemoryGraphDriver) -> None:
    """Assert shortest path from ADDRESS_SET to PAID matches exact expected operations."""
    path = get_shortest_path(populated_driver, "ADDRESS_SET", "PAID")
    assert path == ["POST /orders/{id}/payment", "POST /payments/webhook"]


def test_shortest_path_same_state(populated_driver: InMemoryGraphDriver) -> None:
    """Distance from a state to itself is empty."""
    assert get_shortest_path(populated_driver, "PAID", "PAID") == []


def test_shortest_path_unreachable_returns_empty(populated_driver: InMemoryGraphDriver) -> None:
    """Unreachable target (e.g. backward direction) returns empty path."""
    assert get_shortest_path(populated_driver, "PAID", "CART_ACTIVE") == []
    assert get_shortest_path(populated_driver, "CANCELLED", "PAID") == []


def test_is_state_reachable(populated_driver: InMemoryGraphDriver) -> None:
    """Verify forward reachability and backwards non-reachability."""
    assert is_state_reachable(populated_driver, "ADDRESS_SET", "PAID") is True
    assert is_state_reachable(populated_driver, "ADDRESS_SET", "DELIVERED") is True
    assert is_state_reachable(populated_driver, "ADDRESS_SET", "CANCELLED") is True
    assert is_state_reachable(populated_driver, "PAID", "ORDER_CREATED") is False
    assert is_state_reachable(populated_driver, "CANCELLED", "DELIVERED") is False
    assert is_state_reachable(populated_driver, "PAID", "PAID") is True


def test_get_source_states(populated_driver: InMemoryGraphDriver) -> None:
    """Verify source states returned for given operations."""
    # POST /orders/{id}/payment is valid from ADDRESS_SET
    payment_sources = get_source_states(populated_driver, "POST /orders/{id}/payment")
    assert payment_sources == {"ADDRESS_SET"}

    # POST /orders/{id}/cancel is valid from ORDER_CREATED, ADDRESS_SET, PAYMENT_PENDING, PAID
    cancel_sources = get_source_states(populated_driver, "POST /orders/{id}/cancel")
    assert cancel_sources == {"ORDER_CREATED", "ADDRESS_SET", "PAYMENT_PENDING", "PAID"}

    # Query by transition ID
    t10_sources = get_source_states(populated_driver, "T10")
    assert t10_sources == {"PAID", "DELIVERED"}

    # Unknown operation returns empty set
    assert get_source_states(populated_driver, "DELETE /unknown") == set()


def test_get_legal_transitions(populated_driver: InMemoryGraphDriver) -> None:
    """Verify legal transitions and allowed roles from current state."""
    transitions = get_legal_transitions(populated_driver, "ADDRESS_SET")
    ops = {t["operation"]: t for t in transitions}

    # ADDRESS_SET allows PUT /orders/{id}/address, POST /orders/{id}/payment, and POST /orders/{id}/cancel
    assert "PUT /orders/{id}/address" in ops
    assert ops["PUT /orders/{id}/address"]["allowed_roles"] == ["customer"]
    assert ops["PUT /orders/{id}/address"]["to_state"] == "ADDRESS_SET"

    assert "POST /orders/{id}/payment" in ops
    assert ops["POST /orders/{id}/payment"]["allowed_roles"] == ["customer"]
    assert ops["POST /orders/{id}/payment"]["to_state"] == "PAYMENT_PENDING"

    assert "POST /orders/{id}/cancel" in ops
    assert "customer" in ops["POST /orders/{id}/cancel"]["allowed_roles"]
    assert "support" in ops["POST /orders/{id}/cancel"]["allowed_roles"]
    assert ops["POST /orders/{id}/cancel"]["to_state"] == "CANCELLED"


def test_works_with_session_as_well_as_driver(populated_driver: InMemoryGraphDriver) -> None:
    """Verify query functions accept session object directly."""
    with populated_driver.session() as session:
        path = get_shortest_path(session, "ADDRESS_SET", "PAID")
        assert path == ["POST /orders/{id}/payment", "POST /payments/webhook"]
        assert is_state_reachable(session, "ADDRESS_SET", "PAID") is True
        assert len(get_legal_transitions(session, "ADDRESS_SET")) >= 3
