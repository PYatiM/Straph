"""Tests for ModelTables precomputation and lookup dictionary validation."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest

from services.detector.tables import ModelTables, load_tables

ROOT = Path(__file__).resolve().parents[2]
CHECKOUT_MODEL_PATH = ROOT / "contracts" / "examples" / "model_checkout.json"


@pytest.fixture
def checkout_model_dict() -> dict[str, Any]:
    with open(CHECKOUT_MODEL_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def test_load_tables_performance(checkout_model_dict: dict[str, Any]) -> None:
    """Acceptance criteria: load_tables constructs all tables in < 5ms."""
    start = time.perf_counter()
    tables = load_tables(checkout_model_dict)
    elapsed_ms = (time.perf_counter() - start) * 1000.0
    assert elapsed_ms < 5.0, f"load_tables took {elapsed_ms:.2f}ms, expected < 5ms"
    assert isinstance(tables, ModelTables)


def test_model_tables_mappings(checkout_model_dict: dict[str, Any]) -> None:
    """Validate all core lookup dictionaries against model_checkout.json."""
    tables = load_tables(checkout_model_dict)

    # Initial and terminal states
    assert tables.initial_state == "CART_ACTIVE"
    assert "CANCELLED" in tables.terminal_states
    assert "REFUNDED" in tables.terminal_states

    # Source states
    assert tables.source_states["POST /cart/items"] == {"CART_ACTIVE"}
    assert tables.source_states["POST /orders"] == {"CART_ACTIVE"}
    assert tables.source_states["PUT /orders/{id}/address"] == {"ORDER_CREATED", "ADDRESS_SET"}
    assert tables.source_states["POST /orders/{id}/payment"] == {"ADDRESS_SET"}
    assert tables.source_states["POST /payments/webhook"] == {"PAYMENT_PENDING"}
    assert tables.source_states["POST /orders/{id}/refund"] == {"PAID", "DELIVERED"}

    # Target states
    assert tables.target_states[("CART_ACTIVE", "POST /orders")] == "ORDER_CREATED"
    assert tables.target_states[("ORDER_CREATED", "PUT /orders/{id}/address")] == "ADDRESS_SET"
    assert tables.target_states[("ADDRESS_SET", "PUT /orders/{id}/address")] == "ADDRESS_SET"
    assert tables.target_states[("ADDRESS_SET", "POST /orders/{id}/payment")] == "PAYMENT_PENDING"
    assert tables.target_states[("PAYMENT_PENDING", "POST /payments/webhook")] == "PAID"
    assert tables.target_states[("PAID", "POST /orders/{id}/fulfill")] == "FULFILLMENT_QUEUED"
    assert tables.target_states[("FULFILLMENT_QUEUED", "POST /orders/{id}/ship")] == "SHIPPED"
    assert tables.target_states[("SHIPPED", "POST /orders/{id}/deliver")] == "DELIVERED"

    # One shot operations
    assert "POST /orders/{id}/refund" in tables.one_shot_ops
    assert "/orders/{id}/refund" in tables.one_shot_ops
    assert "POST /orders" not in tables.one_shot_ops

    # Allowed roles
    assert tables.allowed_roles["POST /cart/items"] == {"customer"}
    assert tables.allowed_roles["POST /payments/webhook"] == {"payment-gateway"}
    assert tables.allowed_roles["POST /orders/{id}/refund"] == {"support"}
    assert tables.allowed_roles["POST /orders/{id}/deliver"] == {"carrier", "warehouse"}

    # Reachability
    assert "PAID" in tables.reachability["ADDRESS_SET"]
    assert "SHIPPED" in tables.reachability["ADDRESS_SET"]
    assert "DELIVERED" in tables.reachability["ADDRESS_SET"]
    assert "REFUNDED" in tables.reachability["ADDRESS_SET"]
    assert "CANCELLED" in tables.reachability["ADDRESS_SET"]
    assert "CART_ACTIVE" not in tables.reachability["ADDRESS_SET"]
    assert tables.reachability["CANCELLED"] == set()
    assert tables.reachability["REFUNDED"] == set()

    # Shortest paths
    assert tables.shortest_paths[("ADDRESS_SET", "ADDRESS_SET")] == []
    assert tables.shortest_paths[("ADDRESS_SET", "PAID")] == [
        "POST /orders/{id}/payment",
        "POST /payments/webhook",
    ]
    assert tables.shortest_paths[("ADDRESS_SET", "SHIPPED")] == [
        "POST /orders/{id}/payment",
        "POST /payments/webhook",
        "POST /orders/{id}/fulfill",
        "POST /orders/{id}/ship",
    ]
    assert ("DELIVERED", "CART_ACTIVE") not in tables.shortest_paths
