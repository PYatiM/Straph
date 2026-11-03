"""Tests for classifier skeleton and Rule R4 (Unmodeled Operation)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from services.detector.classifier import ClassificationResult, classify
from services.detector.store import InstanceRecord
from services.detector.tables import ModelTables, load_tables

ROOT = Path(__file__).resolve().parents[2]
CHECKOUT_MODEL_PATH = ROOT / "contracts" / "examples" / "model_checkout.json"


@pytest.fixture
def tables() -> ModelTables:
    with open(CHECKOUT_MODEL_PATH, "r", encoding="utf-8") as f:
        model_dict: dict[str, Any] = json.load(f)
    return load_tables(model_dict)


@pytest.fixture
def instance() -> InstanceRecord:
    return InstanceRecord(
        resource_type="Order",
        resource_id="1001",
        current_state="CART_ACTIVE",
        owner_subject="user-1",
    )


def test_known_operation_passes_r4(instance: InstanceRecord, tables: ModelTables) -> None:
    """Known modeled operations must pass the R4 check."""
    event = {
        "event_id": "00000000-0000-4000-8000-000000000001",
        "operation": {"method": "POST", "path_template": "/orders"},
        "status_code": 201,
        "subject": {"sub": "user-1", "role": "customer"},
    }
    result = classify(instance, event, tables)
    assert result is None  # Benign with respect to R4


def test_known_parameterized_operation_passes_r4(instance: InstanceRecord, tables: ModelTables) -> None:
    """Parameterized operation path /orders/1001/address matches /orders/{id}/address."""
    event = {
        "event_id": "00000000-0000-4000-8000-000000000002",
        "operation": {"method": "PUT", "path_template": "/orders/1001/address"},
        "status_code": 200,
        "subject": {"sub": "user-1", "role": "customer"},
    }
    result = classify(instance, event, tables)
    assert result is None


def test_unknown_operation_triggers_rule_r4(instance: InstanceRecord, tables: ModelTables) -> None:
    """Arbitrary endpoints trigger Rule R4 / UNMODELED."""
    event = {
        "event_id": "00000000-0000-4000-8000-000000000099",
        "operation": {"method": "POST", "path_template": "/unknown/action"},
        "status_code": 404,
        "subject": {"sub": "user-1", "role": "customer"},
    }
    result = classify(instance, event, tables)
    assert isinstance(result, ClassificationResult)
    assert result.is_violation is True
    assert result.label == "UNMODELED"
    assert result.rule == "R4"
    assert result.severity == "attempted"
    assert result.missing == []
    assert result.expected_path == []

    # Acceptance criteria: instance state is not modified
    assert instance.current_state == "CART_ACTIVE"
    assert instance.history == []
    assert instance.tainted is False


def test_arbitrary_method_and_path_trigger_r4(instance: InstanceRecord, tables: ModelTables) -> None:
    """Unmodeled methods or paths trigger R4."""
    event = {
        "event_id": "00000000-0000-4000-8000-000000000088",
        "operation": {"method": "DELETE", "path_template": "/internal/debug"},
        "status_code": 200,
        "subject": {"sub": "attacker", "role": "anonymous"},
    }
    result = classify(instance, event, tables)
    assert result is not None
    assert result.rule == "R4"
    assert result.label == "UNMODELED"
