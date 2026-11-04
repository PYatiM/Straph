"""Tests for legal state advance on accepted 2xx API calls."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from services.detector.classifier import classify
from services.detector.store import InstanceRecord
from services.detector.tables import ModelTables, load_tables
from tests.detector.fixtures.tc1_legal_path import get_tc1_events

ROOT = Path(__file__).resolve().parents[2]
CHECKOUT_MODEL_PATH = ROOT / "contracts" / "examples" / "model_checkout.json"


@pytest.fixture
def tables() -> ModelTables:
    with open(CHECKOUT_MODEL_PATH, "r", encoding="utf-8") as f:
        model_dict: dict[str, Any] = json.load(f)
    return load_tables(model_dict)


def test_tc1_sequential_advance_to_shipped(tables: ModelTables) -> None:
    """Run TC1 events: instance advances from START to SHIPPED with zero alerts."""
    order_instance = InstanceRecord(
        resource_type="Order",
        resource_id="1001",
        current_state="START",
        owner_subject="customer-1001",
    )
    cart_instance = InstanceRecord(
        resource_type="Cart",
        resource_id="cart-1001",
        current_state="START",
        owner_subject="customer-1001",
    )

    events = get_tc1_events()
    expected_state_sequence = [
        ("Cart", "CART_ACTIVE"),
        ("Order", "ORDER_CREATED"),
        ("Order", "ADDRESS_SET"),
        ("Order", "PAYMENT_PENDING"),
        ("Order", "PAID"),
        ("Order", "FULFILLMENT_QUEUED"),
        ("Order", "SHIPPED"),
    ]

    for event, (res_type, expected_state) in zip(events, expected_state_sequence, strict=True):
        inst = order_instance if res_type == "Order" else cart_instance
        result = classify(inst, event, tables)
        assert result is None, f"Unexpected alert for event {event['event_id']}: {result}"
        assert inst.current_state == expected_state

    assert order_instance.current_state == "SHIPPED"
    assert len(order_instance.history) == 6
    assert len(cart_instance.history) == 1


def test_non_2xx_call_does_not_advance_state(tables: ModelTables) -> None:
    """Failed call (400) on a legal operation records history but does not advance state."""
    order = InstanceRecord(
        resource_type="Order",
        resource_id="1001",
        current_state="ORDER_CREATED",
        owner_subject="customer-1001",
    )
    event = {
        "event_id": "00000000-0000-4000-8000-000000000010",
        "operation": {"method": "PUT", "path_template": "/orders/1001/address"},
        "status_code": 400,
        "ts_completed": "2026-10-29T10:02:00.050Z",
        "subject": {"sub": "customer-1001", "role": "customer"},
    }

    result = classify(order, event, tables)
    assert result is None  # Benign, no alert
    assert order.current_state == "ORDER_CREATED"  # State preserved
    assert len(order.history) == 1
    assert order.history[0]["status"] == 400
    assert order.history[0]["operation"] == "PUT /orders/{id}/address"
