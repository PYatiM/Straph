"""Performance and accuracy tests for InMemoryModelGraph cache."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest

from services.model_builder.cache import InMemoryModelGraph

ROOT_DIR = Path(__file__).resolve().parents[2]
CHECKOUT_MODEL_PATH = ROOT_DIR / "contracts" / "examples" / "model_checkout.json"


@pytest.fixture(scope="module")
def checkout_model() -> dict[str, Any]:
    with open(CHECKOUT_MODEL_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="module")
def cached_graph(checkout_model: dict[str, Any]) -> InMemoryModelGraph:
    return InMemoryModelGraph(checkout_model)


def test_structure_populated(cached_graph: InMemoryModelGraph) -> None:
    """Verify dictionary adjacency structures are correctly populated."""
    assert len(cached_graph.states) == 10
    assert len(cached_graph.legal_transitions) > 0
    assert len(cached_graph.reachability_set) == 10
    assert len(cached_graph.shortest_paths) > 0

    # Specifically check T4 from ADDRESS_SET with POST /orders/{id}/payment
    targets = cached_graph.get_legal_next_states("ADDRESS_SET", "POST /orders/{id}/payment")
    assert targets == ["PAYMENT_PENDING"]

    roles = cached_graph.get_allowed_roles("ADDRESS_SET", "POST /orders/{id}/payment")
    assert roles == ["customer"]


def test_shortest_path_matches_graph_queries(cached_graph: InMemoryModelGraph) -> None:
    """Verify precomputed shortest path from ADDRESS_SET to PAID matches exact expected operations."""
    path = cached_graph.get_shortest_path("ADDRESS_SET", "PAID")
    assert path == ["POST /orders/{id}/payment", "POST /payments/webhook"]


def test_reachability(cached_graph: InMemoryModelGraph) -> None:
    """Verify O(1) reachability lookups."""
    assert cached_graph.is_state_reachable("ADDRESS_SET", "PAID") is True
    assert cached_graph.is_state_reachable("ADDRESS_SET", "CANCELLED") is True
    assert cached_graph.is_state_reachable("PAID", "ORDER_CREATED") is False
    assert cached_graph.is_state_reachable("REFUNDED", "PAID") is False
    assert cached_graph.is_state_reachable("PAID", "PAID") is True


def test_source_states_and_single_use(cached_graph: InMemoryModelGraph) -> None:
    """Verify source states and single-use flag lookups."""
    payment_sources = cached_graph.get_source_states("POST /orders/{id}/payment")
    assert payment_sources == {"ADDRESS_SET"}

    # T10 (refund) is single_use=True
    assert cached_graph.is_single_use("T10") is True
    assert cached_graph.is_single_use("POST /orders/{id}/refund") is True

    # T4 (payment) is single_use=False
    assert cached_graph.is_single_use("T4") is False
    assert cached_graph.is_single_use("POST /orders/{id}/payment") is False


def test_lookup_latency_sub_0_1_ms_over_1000_queries(
    cached_graph: InMemoryModelGraph,
) -> None:
    """Assert lookup latency is strictly <0.1 ms per query across 1,000 queries."""
    query_count = 1000
    queries = [
        ("ADDRESS_SET", "POST /orders/{id}/payment"),
        ("PAYMENT_PENDING", "POST /payments/webhook"),
        ("PAID", "POST /orders/{id}/fulfill"),
        ("FULFILLMENT_QUEUED", "POST /orders/{id}/ship"),
        ("SHIPPED", "POST /orders/{id}/deliver"),
    ]

    start = time.perf_counter()
    for i in range(query_count):
        state, op = queries[i % len(queries)]
        _ = cached_graph.get_legal_next_states(state, op)
        _ = cached_graph.is_state_reachable(state, "DELIVERED")
        _ = cached_graph.get_shortest_path(state, "DELIVERED")
    elapsed_total_seconds = time.perf_counter() - start

    # Average latency per iteration (which ran 3 lookups) in milliseconds
    avg_latency_ms = (elapsed_total_seconds / query_count) * 1000.0

    assert avg_latency_ms < 0.1, f"Average lookup took {avg_latency_ms:.4f} ms, expected < 0.1 ms"
