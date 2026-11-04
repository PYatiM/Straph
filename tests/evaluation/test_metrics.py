"""Unit tests for core IDS evaluation metrics calculation module."""

from __future__ import annotations

import uuid

import pytest

from services.evaluation.label_joiner import EvaluationOutcome, JoinedEvaluationRecord
from services.evaluation.labels import ExpectedLabel, ExpectedRule, GroundTruthLabel
from services.evaluation.metrics import calculate_metrics


def _make_record(
    outcome: EvaluationOutcome,
    expected_missing: list[str] | None = None,
    alert_missing: list[str] | None = None,
) -> JoinedEvaluationRecord:
    """Create a synthetic JoinedEvaluationRecord for testing metric formulas."""
    ev_id = str(uuid.uuid4())
    is_violation = outcome in (EvaluationOutcome.TP, EvaluationOutcome.FN)
    exp_missing = list(expected_missing or [])
    obs_missing = list(alert_missing if alert_missing is not None else exp_missing)

    label = GroundTruthLabel(
        event_id=ev_id,
        is_violation=is_violation,
        expected_label=ExpectedLabel.SKIPPED if is_violation else ExpectedLabel.BENIGN,
        expected_rule=ExpectedRule.R2 if is_violation else None,
        missing_ops=exp_missing,
        scenario_id="TEST_METRICS",
        seed=42,
    )

    alert = None
    if outcome in (EvaluationOutcome.TP, EvaluationOutcome.FP):
        alert = {
            "alert_id": str(uuid.uuid4()),
            "event_id": ev_id,
            "label": "SKIPPED",
            "rule": "R2",
            "missing": obs_missing,
        }

    return JoinedEvaluationRecord(
        event_id=ev_id,
        label=label,
        alert=alert,
        outcome=outcome,
        rule_matched=(outcome == EvaluationOutcome.TP),
        label_matched=(outcome == EvaluationOutcome.TP),
        explanation_matched=(outcome == EvaluationOutcome.TP and obs_missing == exp_missing),
    )


def test_calculate_metrics_known_confusion_matrix() -> None:
    """Verify exact metric formulas on a known confusion matrix (TP=8, FP=2, FN=2, TN=88)."""
    records: list[JoinedEvaluationRecord] = []

    # 6 TPs with matching missing_ops explanation, 2 TPs with mismatched missing_ops
    for _ in range(6):
        records.append(
            _make_record(
                EvaluationOutcome.TP,
                expected_missing=["POST /orders/{id}/payment"],
                alert_missing=["POST /orders/{id}/payment"],
            )
        )
    for _ in range(2):
        records.append(
            _make_record(
                EvaluationOutcome.TP,
                expected_missing=["POST /orders/{id}/payment"],
                alert_missing=[],
            )
        )

    # 2 FPs, 2 FNs, 88 TNs -> Total = 100 events
    for _ in range(2):
        records.append(_make_record(EvaluationOutcome.FP))
    for _ in range(2):
        records.append(_make_record(EvaluationOutcome.FN))
    for _ in range(88):
        records.append(_make_record(EvaluationOutcome.TN))

    metrics = calculate_metrics(records)

    assert metrics["tp"] == 8.0
    assert metrics["fp"] == 2.0
    assert metrics["fn"] == 2.0
    assert metrics["tn"] == 88.0
    assert metrics["total_events"] == 100.0

    # precision = 8 / (8 + 2) = 0.8
    assert metrics["precision"] == pytest.approx(0.8)
    # recall = 8 / (8 + 2) = 0.8
    assert metrics["recall"] == pytest.approx(0.8)
    # f1_score = 2 * 0.8 * 0.8 / 1.6 = 0.8
    assert metrics["f1_score"] == pytest.approx(0.8)
    # false_positives_per_1000_events = (2 / 100) * 1000 = 20.0
    assert metrics["false_positives_per_1000_events"] == pytest.approx(20.0)
    # explanation_correctness_rate = 6 / 8 = 0.75
    assert metrics["explanation_correctness_rate"] == pytest.approx(0.75)


def test_calculate_metrics_zero_alerts_guard() -> None:
    """When TP + FP == 0, precision zero-division guard returns 1.0."""
    records = [
        _make_record(EvaluationOutcome.FN),
        _make_record(EvaluationOutcome.TN),
    ]
    metrics = calculate_metrics(records)
    assert metrics["precision"] == 1.0
    assert metrics["recall"] == 0.0
    assert metrics["f1_score"] == 0.0
    assert metrics["false_positives_per_1000_events"] == 0.0
    assert metrics["explanation_correctness_rate"] == 0.0


def test_calculate_metrics_invalid_inputs() -> None:
    """Non-list or invalid record items raise TypeError."""
    with pytest.raises(TypeError, match="records must be a list"):
        calculate_metrics("not-a-list")  # type: ignore[arg-type]

    with pytest.raises(TypeError, match="Each record must be a JoinedEvaluationRecord"):
        calculate_metrics([{"bad": "item"}])  # type: ignore[list-item]
