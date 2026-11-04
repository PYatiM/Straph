"""Unit tests for alert feature extraction and ground-truth leakage rejection."""

from __future__ import annotations

from typing import Any

import pytest

from services.ranker.features import extract_features


@pytest.fixture
def sample_alert() -> dict[str, Any]:
    """Provide standard contract-compliant alert dict."""
    return {
        "alert_id": "11111111-1111-4111-8111-111111111111",
        "event_id": "22222222-2222-4222-8222-222222222222",
        "timestamp": "2026-11-04T10:00:00Z",
        "label": "SKIPPED",
        "rule": "R2",
        "severity": "accepted",
        "subject": {"issuer": "straph-auth", "sub": "cust-1", "role": "customer"},
        "resource": {"type": "Order", "id": "1001"},
        "expected_path": ["POST /orders/{id}/payment", "POST /orders/{id}/fulfill"],
        "observed": {
            "operation": "POST /orders/{id}/fulfill",
            "state": "ADDRESS_SET",
            "status_code": 200,
        },
        "missing": ["POST /orders/{id}/payment"],
        "owasp_class": "BLA6",
        "flags": {"tainted": True, "late": False, "retry": False},
        "model_version": "v1.0.0",
    }


@pytest.fixture
def sample_history() -> list[dict[str, Any]]:
    """Provide realistic instance history entries."""
    return [
        {"operation": "POST /cart/items", "status": 200, "ts": "2026-11-04T09:58:00Z"},
        {"operation": "POST /orders", "status": 201, "ts": "2026-11-04T09:59:00Z"},
        {"operation": "PUT /orders/{id}/address", "status": 200, "ts": "2026-11-04T10:00:00Z"},
    ]


def test_feature_vector_dimensions_and_types(
    sample_alert: dict[str, Any],
    sample_history: list[dict[str, Any]],
) -> None:
    """Features dictionary must contain exactly the 6 required numeric features."""
    clean_stats = {"POST /orders/{id}/fulfill": 0.05}
    features = extract_features(sample_alert, sample_history, clean_stats)

    expected_keys = {
        "rule_severity_code",
        "is_accepted",
        "has_role_mismatch",
        "missing_steps_count",
        "history_length",
        "transition_rarity",
    }
    assert set(features.keys()) == expected_keys
    for k, v in features.items():
        assert isinstance(v, float), f"Feature {k} must be float, got {type(v).__name__}"

    assert features["rule_severity_code"] == 2.0
    assert features["is_accepted"] == 1.0
    assert features["has_role_mismatch"] == 0.0
    assert features["missing_steps_count"] == 1.0
    assert features["history_length"] == 3.0
    assert features["transition_rarity"] == 0.05


@pytest.mark.parametrize(
    ("rule", "expected_code"),
    [
        ("R0", 4.0),
        ("R1", 3.0),
        ("R2", 2.0),
        ("R3", 1.0),
        ("R4", 0.5),
        ("UNKNOWN_RULE", 0.0),
    ],
)
def test_rule_severity_mapping(
    sample_alert: dict[str, Any],
    sample_history: list[dict[str, Any]],
    rule: str,
    expected_code: float,
) -> None:
    """Verify exact rule severity float codes according to approved mapping."""
    sample_alert["rule"] = rule
    features = extract_features(sample_alert, sample_history)
    assert features["rule_severity_code"] == expected_code


def test_is_accepted_flag(
    sample_alert: dict[str, Any],
    sample_history: list[dict[str, Any]],
) -> None:
    """is_accepted must be 1.0 for accepted, 0.0 for attempted."""
    sample_alert["severity"] = "accepted"
    assert extract_features(sample_alert, sample_history)["is_accepted"] == 1.0

    sample_alert["severity"] = "attempted"
    assert extract_features(sample_alert, sample_history)["is_accepted"] == 0.0


def test_has_role_mismatch(
    sample_alert: dict[str, Any],
    sample_history: list[dict[str, Any]],
) -> None:
    """has_role_mismatch must be 1.0 only for IDENTITY_MISMATCH label."""
    sample_alert["label"] = "IDENTITY_MISMATCH"
    assert extract_features(sample_alert, sample_history)["has_role_mismatch"] == 1.0

    sample_alert["label"] = "SKIPPED"
    assert extract_features(sample_alert, sample_history)["has_role_mismatch"] == 0.0


def test_missing_steps_and_history_length(
    sample_alert: dict[str, Any],
    sample_history: list[dict[str, Any]],
) -> None:
    """missing_steps_count and history_length must reflect true lengths."""
    sample_alert["missing"] = ["step1", "step2", "step3"]
    features = extract_features(sample_alert, sample_history)
    assert features["missing_steps_count"] == 3.0
    assert features["history_length"] == 3.0

    sample_alert["missing"] = []
    features_empty = extract_features(sample_alert, [])
    assert features_empty["missing_steps_count"] == 0.0
    assert features_empty["history_length"] == 0.0


def test_transition_rarity_lookup(
    sample_alert: dict[str, Any],
    sample_history: list[dict[str, Any]],
) -> None:
    """transition_rarity must be looked up from clean_stats or default to 0.0."""
    # Lookup by transition key state->operation
    clean_stats = {"ADDRESS_SET->POST /orders/{id}/fulfill": 0.42}
    features = extract_features(sample_alert, sample_history, clean_stats)
    assert features["transition_rarity"] == 0.42

    # Fallback to operation
    clean_stats_op = {"POST /orders/{id}/fulfill": 0.15}
    features_op = extract_features(sample_alert, sample_history, clean_stats_op)
    assert features_op["transition_rarity"] == 0.15

    # Default without stats
    features_none = extract_features(sample_alert, sample_history, None)
    assert features_none["transition_rarity"] == 0.0


@pytest.mark.parametrize("leak_field", ["is_violation", "expected_label", "expected_rule"])
def test_leakage_rejection_in_alert(
    sample_alert: dict[str, Any],
    sample_history: list[dict[str, Any]],
    leak_field: str,
) -> None:
    """extract_features must raise ValueError if ground-truth fields are passed in alert."""
    sample_alert[leak_field] = True if leak_field == "is_violation" else "SKIPPED"
    with pytest.raises(ValueError, match="Ground-truth label leakage detected"):
        extract_features(sample_alert, sample_history)


@pytest.mark.parametrize("leak_field", ["is_violation", "expected_label"])
def test_leakage_rejection_in_clean_stats(
    sample_alert: dict[str, Any],
    sample_history: list[dict[str, Any]],
    leak_field: str,
) -> None:
    """extract_features must raise ValueError if ground-truth fields are passed in clean_stats."""
    clean_stats = {leak_field: True}
    with pytest.raises(ValueError, match="Ground-truth label leakage detected"):
        extract_features(sample_alert, sample_history, clean_stats)
