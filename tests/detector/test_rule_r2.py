"""Tests for Rule R2 (Skipped Intermediate Steps)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from services.detector.classifier import classify
from services.detector.store import InstanceRecord
from services.detector.tables import ModelTables, load_tables
from tests.detector.fixtures.tc2_skipped_payment import get_tc2_events

ROOT = Path(__file__).resolve().parents[2]
CHECKOUT_MODEL_PATH = ROOT / "contracts" / "examples" / "model_checkout.json"


@pytest.fixture
def tables() -> ModelTables:
    with open(CHECKOUT_MODEL_PATH, "r", encoding="utf-8") as f:
        model_dict: dict[str, Any] = json.load(f)
    return load_tables(model_dict)


def test_tc2_skipped_payment_triggers_r2(tables: ModelTables) -> None:
    """Verify TC2 skipped payment triggers Rule R2 SKIPPED with missing operations."""
    order = InstanceRecord(
        resource_type="Order",
        resource_id="1002",
        current_state="START",
        owner_subject="customer-1002",
    )

    events = get_tc2_events()

    # Event 1 (POST /orders): legal advance
    res0 = classify(order, events[0], tables)
    assert res0 is None
    assert order.current_state == "ORDER_CREATED"

    # Event 2 (PUT /orders/1002/address): legal advance
    res1 = classify(order, events[1], tables)
    assert res1 is None
    assert order.current_state == "ADDRESS_SET"

    # Event 3 (POST /orders/1002/fulfill): skips payment and webhook
    alert = classify(order, events[2], tables)
    assert alert is not None
    assert alert.is_violation is True
    assert alert.label == "SKIPPED"
    assert alert.rule == "R2"
    assert alert.severity == "accepted"
    assert alert.missing == [
        "POST /orders/{id}/payment",
        "POST /payments/webhook",
    ]
    assert alert.expected_path == [
        "POST /orders/{id}/payment",
        "POST /payments/webhook",
        "POST /orders/{id}/fulfill",
    ]


def test_skipped_non_2xx_has_attempted_severity(tables: ModelTables) -> None:
    """Attempted skip returning 400 has severity='attempted'."""
    order = InstanceRecord(
        resource_type="Order",
        resource_id="1002",
        current_state="ADDRESS_SET",
        owner_subject="customer-1002",
    )
    event = {
        "event_id": "00000000-0000-4000-8000-000000000099",
        "operation": {"method": "POST", "path_template": "/orders/1002/fulfill"},
        "status_code": 400,
        "subject": {"sub": "warehouse-worker-1", "role": "warehouse"},
    }

    result = classify(order, event, tables)
    assert result is not None
    assert result.is_violation is True
    assert result.label == "SKIPPED"
    assert result.rule == "R2"
    assert result.severity == "attempted"
    assert "POST /orders/{id}/payment" in result.missing
