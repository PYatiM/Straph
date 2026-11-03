"""Tests for AlertBuilder and OWASP BLA mapping."""

from __future__ import annotations

import uuid
from typing import Any

import jsonschema
import pytest

from services.detector.alerts import AlertBuilder
from services.detector.classifier import ClassificationResult
from services.detector.store import InstanceRecord


@pytest.fixture
def builder() -> AlertBuilder:
    return AlertBuilder()


@pytest.fixture
def instance() -> InstanceRecord:
    return InstanceRecord(
        resource_type="Order",
        resource_id="1001",
        current_state="ORDER_CREATED",
        owner_subject="user-1",
    )


def _base_event() -> dict[str, Any]:
    return {
        "event_id": str(uuid.uuid4()),
        "ts_completed": "2026-10-29T10:00:00.000Z",
        "operation": {"method": "POST", "path_template": "/orders/1001/fulfill"},
        "status_code": 200,
        "subject": {"sub": "user-1", "role": "customer", "issuer": "straph-test"},
    }


def test_build_alert_rule_r0(builder: AlertBuilder, instance: InstanceRecord) -> None:
    """Generate R0 alert with secondary label and verify BLA9 mapping and schema validity."""
    event = _base_event()
    classification = ClassificationResult(
        is_violation=True,
        label="IDENTITY_MISMATCH",
        rule="R0",
        secondary_label="CANCELLED",
        severity="accepted",
        missing=[],
        expected_path=["POST /orders", "POST /orders/{id}/cancel"],
    )

    alert = builder.build_alert(instance, event, classification, "checkout-v0")
    assert alert["rule"] == "R0"
    assert alert["label"] == "IDENTITY_MISMATCH"
    assert alert["owasp_class"] == "BLA9"
    assert alert["secondary_label"] == "CANCELLED"
    assert alert["severity"] == "accepted"


def test_build_alert_rule_r1(builder: AlertBuilder, instance: InstanceRecord) -> None:
    """Generate R1 alert and verify BLA5 mapping and schema validity."""
    event = _base_event()
    classification = ClassificationResult(
        is_violation=True,
        label="REPLAYED",
        rule="R1",
        severity="accepted",
        missing=[],
        expected_path=[],
    )

    alert = builder.build_alert(instance, event, classification, "checkout-v0")
    assert alert["rule"] == "R1"
    assert alert["label"] == "REPLAYED"
    assert alert["owasp_class"] == "BLA5"


def test_build_alert_rule_r2(builder: AlertBuilder, instance: InstanceRecord) -> None:
    """Generate R2 alert and verify BLA6 mapping, missing ops, and schema validity."""
    event = _base_event()
    classification = ClassificationResult(
        is_violation=True,
        label="SKIPPED",
        rule="R2",
        severity="accepted",
        missing=["POST /orders/{id}/payment", "POST /payments/webhook"],
        expected_path=["POST /orders/{id}/payment", "POST /payments/webhook", "POST /orders/{id}/fulfill"],
    )

    alert = builder.build_alert(instance, event, classification, "checkout-v0")
    assert alert["rule"] == "R2"
    assert alert["label"] == "SKIPPED"
    assert alert["owasp_class"] == "BLA6"
    assert len(alert["missing"]) == 2


def test_build_alert_rule_r3(builder: AlertBuilder, instance: InstanceRecord) -> None:
    """Generate R3 alert and verify BLA6 mapping and schema validity."""
    event = _base_event()
    classification = ClassificationResult(
        is_violation=True,
        label="OUT_OF_ORDER",
        rule="R3",
        severity="accepted",
        missing=[],
        expected_path=[],
    )

    alert = builder.build_alert(instance, event, classification, "checkout-v0")
    assert alert["rule"] == "R3"
    assert alert["label"] == "OUT_OF_ORDER"
    assert alert["owasp_class"] == "BLA6"


def test_build_alert_rule_r4(builder: AlertBuilder, instance: InstanceRecord) -> None:
    """Generate R4 alert and verify BLA10 mapping and schema validity."""
    event = _base_event()
    event["operation"] = {"method": "POST", "path_template": "/unknown/action"}
    classification = ClassificationResult(
        is_violation=True,
        label="UNMODELED",
        rule="R4",
        severity="attempted",
        missing=[],
        expected_path=[],
    )

    alert = builder.build_alert(instance, event, classification, "checkout-v0")
    assert alert["rule"] == "R4"
    assert alert["label"] == "UNMODELED"
    assert alert["owasp_class"] == "BLA10"
    assert alert["severity"] == "attempted"


def test_invalid_payload_raises_schema_validation_error(builder: AlertBuilder, instance: InstanceRecord) -> None:
    """Verify corrupted classification fields raise ValidationError."""
    event = _base_event()
    # Invalid rule and label not in schema enum
    classification = ClassificationResult(
        is_violation=True,
        label="INVALID_LABEL",
        rule="R99",
        severity="attempted",
    )

    with pytest.raises(jsonschema.ValidationError):
        builder.build_alert(instance, event, classification, "checkout-v0")
