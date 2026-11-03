"""Tests for instance state tainting and resynchronization on accepted violations."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from services.detector.classifier import classify
from services.detector.store import InstanceRecord
from services.detector.tables import ModelTables, load_tables

ROOT = Path(__file__).resolve().parents[2]
CHECKOUT_MODEL_PATH = ROOT / "contracts" / "examples" / "model_checkout.json"


@pytest.fixture
def tables() -> ModelTables:
    with open(CHECKOUT_MODEL_PATH, "r", encoding="utf-8") as f:
        model_dict: dict[str, Any] = json.load(f)
    return load_tables(model_dict)


def test_taint_and_resync_on_skipped_payment(tables: ModelTables) -> None:
    """Verify accepted skip moves state to FULFILLMENT_QUEUED with tainted=True,

    and subsequent legal call executes cleanly with zero alerts.
    """
    order = InstanceRecord(
        resource_type="Order",
        resource_id="1002",
        current_state="ADDRESS_SET",
        owner_subject="customer-1002",
        tainted=False,
    )

    # 1. Skipped payment call accepted (POST /orders/1002/fulfill returning 200)
    fulfill_event = {
        "event_id": "00000002-0000-4000-8000-000000000003",
        "operation": {"method": "POST", "path_template": "/orders/1002/fulfill"},
        "status_code": 200,
        "ts_completed": "2026-10-29T11:02:00.050Z",
        "subject": {"sub": "warehouse-worker-1", "role": "warehouse"},
    }

    result = classify(order, fulfill_event, tables)
    assert result is not None
    assert result.is_violation is True
    assert result.label == "SKIPPED"
    assert result.rule == "R2"
    assert result.severity == "accepted"

    # Invariant 1: Instance marked tainted and state resynchronized to FULFILLMENT_QUEUED
    assert order.tainted is True
    assert order.current_state == "FULFILLMENT_QUEUED"
    assert len(order.history) == 1
    assert order.history[-1]["tainted"] is True

    # 2. Subsequent legal call POST /orders/1002/ship from FULFILLMENT_QUEUED
    ship_event = {
        "event_id": "00000002-0000-4000-8000-000000000004",
        "operation": {"method": "POST", "path_template": "/orders/1002/ship"},
        "status_code": 200,
        "ts_completed": "2026-10-29T11:03:00.050Z",
        "subject": {"sub": "warehouse-worker-1", "role": "warehouse"},
    }

    ship_result = classify(order, ship_event, tables)

    # Invariant 2: Subsequent legal call executes legally and generates ZERO new alerts
    assert ship_result is None
    assert order.current_state == "SHIPPED"
    assert order.tainted is True
    assert len(order.history) == 2


def test_rejected_violation_does_not_resync_state(tables: ModelTables) -> None:
    """Non-2xx rejected violation does NOT taint instance or resynchronize state."""
    order = InstanceRecord(
        resource_type="Order",
        resource_id="1002",
        current_state="ADDRESS_SET",
        owner_subject="customer-1002",
        tainted=False,
    )
    failed_fulfill = {
        "event_id": "00000002-0000-4000-8000-000000000005",
        "operation": {"method": "POST", "path_template": "/orders/1002/fulfill"},
        "status_code": 403,
        "ts_completed": "2026-10-29T11:02:00.050Z",
        "subject": {"sub": "warehouse-worker-1", "role": "warehouse"},
    }

    result = classify(order, failed_fulfill, tables)
    assert result is not None
    assert result.severity == "attempted"
    assert order.tainted is False
    assert order.current_state == "ADDRESS_SET"
