"""Tests for Rule R1 (Replayed One-Shot Operation)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from services.detector.classifier import ClassificationResult, classify
from services.detector.store import InstanceRecord
from services.detector.tables import ModelTables, load_tables
from tests.detector.fixtures.tc4_double_refund import get_tc4_events

ROOT = Path(__file__).resolve().parents[2]
CHECKOUT_MODEL_PATH = ROOT / "contracts" / "examples" / "model_checkout.json"


@pytest.fixture
def tables() -> ModelTables:
    with open(CHECKOUT_MODEL_PATH, "r", encoding="utf-8") as f:
        model_dict: dict[str, Any] = json.load(f)
    return load_tables(model_dict)


def test_tc4_double_refund_triggers_r1(tables: ModelTables) -> None:
    """Verify TC4 double refund triggers Rule R1 REPLAYED on the second refund call."""
    order_instance = InstanceRecord(
        resource_type="Order",
        resource_id="1004",
        current_state="START",
        owner_subject="customer-1004",
    )
    cart_instance = InstanceRecord(
        resource_type="Cart",
        resource_id="cart-1004",
        current_state="START",
        owner_subject="customer-1004",
    )

    events = get_tc4_events()
    alerts: list[tuple[int, ClassificationResult]] = []

    for idx, event in enumerate(events, start=1):
        inst = order_instance if event.get("resource_type") == "Order" else cart_instance
        res = classify(inst, event, tables)
        if res is not None and res.is_violation:
            alerts.append((idx, res))

    # Exactly one alert on the 10th event
    assert len(alerts) == 1
    event_idx, alert = alerts[0]
    assert event_idx == 10
    assert alert.label == "REPLAYED"
    assert alert.rule == "R1"
    assert alert.severity == "accepted"
    assert alert.missing == []
    assert alert.expected_path == []


def test_replayed_non_2xx_has_attempted_severity(tables: ModelTables) -> None:
    """Replaying a one-shot operation with non-2xx status results in severity='attempted'."""
    order = InstanceRecord(
        resource_type="Order",
        resource_id="1004",
        current_state="REFUNDED",
        history=[{"operation": "POST /orders/{id}/refund", "status": 200}],
        owner_subject="customer-1004",
    )
    event = {
        "event_id": "00000000-0000-4000-8000-000000000099",
        "operation": {"method": "POST", "path_template": "/orders/1004/refund"},
        "status_code": 409,
        "subject": {"sub": "support-agent-1", "role": "support"},
    }

    result = classify(order, event, tables)
    assert result is not None
    assert result.is_violation is True
    assert result.label == "REPLAYED"
    assert result.rule == "R1"
    assert result.severity == "attempted"
