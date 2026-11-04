"""Unit tests for ground-truth label joiner and alert alignment validator."""

from __future__ import annotations

from typing import Any

import pytest

from services.evaluation.label_joiner import (
    EvaluationOutcome,
    JoinedEvaluationRecord,
    join_alerts_with_labels,
)
from services.evaluation.labels import ExpectedLabel, ExpectedRule, GroundTruthLabel

EV_TP = "00000100-0000-4000-8000-000000000001"
EV_FP = "00000100-0000-4000-8000-000000000002"
EV_FN = "00000100-0000-4000-8000-000000000003"
EV_TN = "00000100-0000-4000-8000-000000000004"


def _make_toy_labels() -> list[GroundTruthLabel]:
    """Create 4 hand-labeled toy ground-truth records covering TP, FP, FN, TN."""
    return [
        GroundTruthLabel(
            event_id=EV_TP,
            is_violation=True,
            expected_label=ExpectedLabel.SKIPPED,
            expected_rule=ExpectedRule.R2,
            missing_ops=["POST /orders/{id}/payment", "POST /payments/webhook"],
            scenario_id="TOY_TP",
            seed=42,
        ),
        GroundTruthLabel(
            event_id=EV_FP,
            is_violation=False,
            expected_label=ExpectedLabel.BENIGN,
            expected_rule=None,
            missing_ops=[],
            scenario_id="TOY_FP",
            seed=42,
        ),
        GroundTruthLabel(
            event_id=EV_FN,
            is_violation=True,
            expected_label=ExpectedLabel.REPLAYED,
            expected_rule=ExpectedRule.R1,
            missing_ops=[],
            scenario_id="TOY_FN",
            seed=42,
        ),
        GroundTruthLabel(
            event_id=EV_TN,
            is_violation=False,
            expected_label=ExpectedLabel.BENIGN,
            expected_rule=None,
            missing_ops=[],
            scenario_id="TOY_TN",
            seed=42,
        ),
    ]


def _make_toy_alerts() -> list[dict[str, Any]]:
    """Create toy alerts for EV_TP (true positive) and EV_FP (false positive)."""
    return [
        {
            "alert_id": "00000100-0000-4000-8000-0000000000a1",
            "event_id": EV_TP,
            "label": "SKIPPED",
            "rule": "R2",
            "missing": ["POST /orders/{id}/payment", "POST /payments/webhook"],
        },
        {
            "alert_id": "00000100-0000-4000-8000-0000000000a2",
            "event_id": EV_FP,
            "label": "OUT_OF_ORDER",
            "rule": "R3",
            "missing": [],
        },
    ]


def test_join_alerts_with_labels_tp_fp_fn_tn() -> None:
    """Verify exact TP, FP, FN, TN classification on hand-labeled toy test cases."""
    labels = _make_toy_labels()
    alerts = _make_toy_alerts()

    records = join_alerts_with_labels(alerts, labels)
    assert len(records) == 4
    assert all(isinstance(r, JoinedEvaluationRecord) for r in records)

    # Record 0: TP
    rec_tp = records[0]
    assert rec_tp.event_id == EV_TP
    assert rec_tp.outcome == EvaluationOutcome.TP
    assert rec_tp.is_tp is True
    assert rec_tp.is_fp is False
    assert rec_tp.is_fn is False
    assert rec_tp.is_tn is False
    assert rec_tp.alert is not None
    assert rec_tp.rule_matched is True
    assert rec_tp.label_matched is True
    assert rec_tp.explanation_matched is True

    # Record 1: FP
    rec_fp = records[1]
    assert rec_fp.event_id == EV_FP
    assert rec_fp.outcome == EvaluationOutcome.FP
    assert rec_fp.is_fp is True
    assert rec_fp.is_tp is False
    assert rec_fp.alert is not None
    assert rec_fp.rule_matched is False
    assert rec_fp.label_matched is False
    assert rec_fp.explanation_matched is False

    # Record 2: FN
    rec_fn = records[2]
    assert rec_fn.event_id == EV_FN
    assert rec_fn.outcome == EvaluationOutcome.FN
    assert rec_fn.is_fn is True
    assert rec_fn.alert is None
    assert rec_fn.rule_matched is False
    assert rec_fn.label_matched is False
    assert rec_fn.explanation_matched is False

    # Record 3: TN
    rec_tn = records[3]
    assert rec_tn.event_id == EV_TN
    assert rec_tn.outcome == EvaluationOutcome.TN
    assert rec_tn.is_tn is True
    assert rec_tn.alert is None
    assert rec_tn.rule_matched is False
    assert rec_tn.label_matched is False
    assert rec_tn.explanation_matched is False


def test_join_alerts_with_labels_strict_rule_match() -> None:
    """Verify strict_rule_match mode downgrades mismatched rule/label alert to FN."""
    label = GroundTruthLabel(
        event_id=EV_TP,
        is_violation=True,
        expected_label=ExpectedLabel.SKIPPED,
        expected_rule=ExpectedRule.R2,
        missing_ops=["POST /orders/{id}/payment", "POST /payments/webhook"],
        scenario_id="TOY_STRICT",
        seed=1,
    )
    wrong_rule_alert = [
        {
            "alert_id": "00000100-0000-4000-8000-0000000000a9",
            "event_id": EV_TP,
            "label": "OUT_OF_ORDER",
            "rule": "R3",
            "missing": [],
        }
    ]

    # Default mode treats anomaly detection on violation event as TP (with explanation_matched=False)
    rec_default = join_alerts_with_labels(wrong_rule_alert, [label])[0]
    assert rec_default.outcome == EvaluationOutcome.TP
    assert rec_default.rule_matched is False
    assert rec_default.label_matched is False
    assert rec_default.explanation_matched is False

    # Strict rule match requires matching rule or label
    rec_strict = join_alerts_with_labels(wrong_rule_alert, [label], strict_rule_match=True)[0]
    assert rec_strict.outcome == EvaluationOutcome.FN


def test_join_alerts_with_labels_type_validation() -> None:
    """Invalid input types raise TypeError."""
    with pytest.raises(TypeError, match="alerts must be a list"):
        join_alerts_with_labels("invalid", [])  # type: ignore[arg-type]

    with pytest.raises(TypeError, match="labels must be a list"):
        join_alerts_with_labels([], "invalid")  # type: ignore[arg-type]

    with pytest.raises(TypeError, match="Each alert must be a dict"):
        join_alerts_with_labels(["not-a-dict"], [])  # type: ignore[list-item]
