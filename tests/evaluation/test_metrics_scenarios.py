"""Metric calculation test suite on hand-verified synthetic datasets (Prompt P102).

Verifies stability and exactness of metrics calculations across extreme performance
distributions:
1. Perfect detector: 100 TPs, 0 FPs, 0 FNs -> precision=1.0, recall=1.0, f1=1.0.
2. Broken detector: 0 TPs, 50 FNs -> recall=0.0.
3. Noisy detector: 10 TPs, 90 FPs -> precision=0.1.
4. Empty test stream: safe exit without division-by-zero exceptions.
"""

from __future__ import annotations

import uuid

import pytest

from services.evaluation.label_joiner import EvaluationOutcome, JoinedEvaluationRecord
from services.evaluation.labels import ExpectedLabel, ExpectedRule, GroundTruthLabel
from services.evaluation.metrics import calculate_metrics


def _build_synthetic_records(
    *,
    tp: int = 0,
    fp: int = 0,
    fn: int = 0,
    tn: int = 0,
) -> list[JoinedEvaluationRecord]:
    """Build a synthetic list of JoinedEvaluationRecord with exact confusion counts."""
    records: list[JoinedEvaluationRecord] = []

    outcomes = (
        [(EvaluationOutcome.TP, True)] * tp
        + [(EvaluationOutcome.FP, False)] * fp
        + [(EvaluationOutcome.FN, True)] * fn
        + [(EvaluationOutcome.TN, False)] * tn
    )

    for outcome, is_violation in outcomes:
        ev_id = str(uuid.uuid4())
        missing = ["POST /orders/{id}/payment"] if is_violation else []
        label = GroundTruthLabel(
            event_id=ev_id,
            is_violation=is_violation,
            expected_label=ExpectedLabel.SKIPPED if is_violation else ExpectedLabel.BENIGN,
            expected_rule=ExpectedRule.R2 if is_violation else None,
            missing_ops=missing,
            scenario_id="SCENARIO_SYNTHETIC",
            seed=42,
        )
        alert = None
        if outcome in (EvaluationOutcome.TP, EvaluationOutcome.FP):
            alert = {
                "alert_id": str(uuid.uuid4()),
                "event_id": ev_id,
                "label": "SKIPPED",
                "rule": "R2",
                "missing": list(missing),
            }
        records.append(
            JoinedEvaluationRecord(
                event_id=ev_id,
                label=label,
                alert=alert,
                outcome=outcome,
                rule_matched=(outcome == EvaluationOutcome.TP),
                label_matched=(outcome == EvaluationOutcome.TP),
                explanation_matched=(outcome == EvaluationOutcome.TP),
            )
        )

    return records


def test_perfect_detector_100_tp_0_fp_0_fn() -> None:
    """1. Perfect detector: 100 TPs, 0 FPs, 0 FNs -> precision=1.0, recall=1.0, f1=1.0."""
    records = _build_synthetic_records(tp=100, fp=0, fn=0, tn=200)
    metrics = calculate_metrics(records)

    assert metrics["tp"] == 100.0
    assert metrics["fp"] == 0.0
    assert metrics["fn"] == 0.0
    assert metrics["precision"] == pytest.approx(1.0)
    assert metrics["recall"] == pytest.approx(1.0)
    assert metrics["f1_score"] == pytest.approx(1.0)
    assert metrics["false_positives_per_1000_events"] == pytest.approx(0.0)
    assert metrics["explanation_correctness_rate"] == pytest.approx(1.0)


def test_broken_detector_0_tp_50_fn() -> None:
    """2. Broken detector: 0 TPs, 50 FNs -> recall=0.0, f1=0.0."""
    records = _build_synthetic_records(tp=0, fp=0, fn=50, tn=150)
    metrics = calculate_metrics(records)

    assert metrics["tp"] == 0.0
    assert metrics["fn"] == 50.0
    assert metrics["recall"] == pytest.approx(0.0)
    assert metrics["f1_score"] == pytest.approx(0.0)
    assert metrics["precision"] == pytest.approx(1.0)
    assert metrics["explanation_correctness_rate"] == pytest.approx(0.0)


def test_noisy_detector_10_tp_90_fp() -> None:
    """3. Noisy detector: 10 TPs, 90 FPs -> precision=0.1."""
    records = _build_synthetic_records(tp=10, fp=90, fn=0, tn=0)
    metrics = calculate_metrics(records)

    assert metrics["tp"] == 10.0
    assert metrics["fp"] == 90.0
    assert metrics["precision"] == pytest.approx(0.1)
    assert metrics["recall"] == pytest.approx(1.0)
    assert metrics["f1_score"] == pytest.approx(2 * 0.1 * 1.0 / 1.1)
    assert metrics["false_positives_per_1000_events"] == pytest.approx(900.0)


def test_empty_test_stream_safe_exit() -> None:
    """4. Empty test stream: safe exit without division-by-zero exceptions."""
    metrics = calculate_metrics([])

    assert metrics["tp"] == 0.0
    assert metrics["fp"] == 0.0
    assert metrics["fn"] == 0.0
    assert metrics["tn"] == 0.0
    assert metrics["total_events"] == 0.0
    assert metrics["precision"] == pytest.approx(1.0)
    assert metrics["recall"] == pytest.approx(1.0)
    assert metrics["f1_score"] == pytest.approx(1.0)
    assert metrics["false_positives_per_1000_events"] == pytest.approx(0.0)
    assert metrics["explanation_correctness_rate"] == pytest.approx(0.0)
