"""Tests for Rule R0 (Identity and Ownership Mismatch) with secondary step label."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from services.detector.classifier import classify
from services.detector.store import InstanceRecord
from services.detector.tables import ModelTables, load_tables
from tests.detector.fixtures.tc5_other_user_order import get_tc5_events
from tests.detector.fixtures.tc6_wrong_role import get_tc6_events

ROOT = Path(__file__).resolve().parents[2]
CHECKOUT_MODEL_PATH = ROOT / "contracts" / "examples" / "model_checkout.json"


@pytest.fixture
def tables() -> ModelTables:
    with open(CHECKOUT_MODEL_PATH, "r", encoding="utf-8") as f:
        model_dict: dict[str, Any] = json.load(f)
    return load_tables(model_dict)


def test_tc5_bola_cancel_triggers_r0_with_secondary_label(tables: ModelTables) -> None:
    """Verify TC5 (BOLA cancel by another customer) triggers Rule R0 with secondary_label CANCELLED."""
    order = InstanceRecord(
        resource_type="Order",
        resource_id="1005",
        current_state="START",
        owner_subject="anonymous",
    )

    events = get_tc5_events()

    # Event 1: POST /orders by user-alpha (customer)
    res1 = classify(order, events[0], tables)
    assert res1 is None
    assert order.current_state == "ORDER_CREATED"
    assert order.owner_subject == "user-alpha"

    # Event 2: POST /orders/1005/cancel by user-bravo (customer) -> BOLA violation
    alert = classify(order, events[1], tables)
    assert alert is not None
    assert alert.is_violation is True
    assert alert.label == "IDENTITY_MISMATCH"
    assert alert.rule == "R0"
    assert alert.secondary_label == "CANCELLED"
    assert alert.severity == "accepted"


def test_tc6_wrong_role_fulfill_triggers_r0(tables: ModelTables) -> None:
    """Verify TC6 (customer calling warehouse-only fulfill) triggers Rule R0 with secondary_label FULFILLED."""
    order = InstanceRecord(
        resource_type="Order",
        resource_id="1006",
        current_state="START",
        owner_subject="user-charlie",
    )
    cart = InstanceRecord(
        resource_type="Cart",
        resource_id="cart-1006",
        current_state="START",
        owner_subject="user-charlie",
    )

    events = get_tc6_events()
    for event in events[:5]:
        inst = order if event.get("resource_type") == "Order" else cart
        res = classify(inst, event, tables)
        assert res is None

    assert order.current_state == "PAID"

    # Event 6: POST /orders/1006/fulfill by user-charlie with role customer
    alert = classify(order, events[5], tables)
    assert alert is not None
    assert alert.is_violation is True
    assert alert.label == "IDENTITY_MISMATCH"
    assert alert.rule == "R0"
    assert alert.secondary_label == "FULFILLED"
    assert alert.severity == "accepted"


def test_cross_object_role_allowed(tables: ModelTables) -> None:
    """Authorized cross-object role (support) can cancel without triggering identity mismatch."""
    order = InstanceRecord(
        resource_type="Order",
        resource_id="1005",
        current_state="ORDER_CREATED",
        owner_subject="user-alpha",
    )
    event = {
        "event_id": "00000000-0000-4000-8000-000000000011",
        "operation": {"method": "POST", "path_template": "/orders/1005/cancel"},
        "status_code": 200,
        "subject": {"sub": "support-agent-9", "role": "support"},
    }

    # Support role is in CROSS_OBJECT_ROLES and allowed for cancel -> benign advance
    res = classify(order, event, tables)
    assert res is None
    assert order.current_state == "CANCELLED"
