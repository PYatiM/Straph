"""Core IDS evaluation metrics calculation module.

Computes precision, recall, F1 score, false positives per 1,000 events, and
explanation correctness rate from a list of JoinedEvaluationRecord instances.
"""

from __future__ import annotations

from services.evaluation.label_joiner import EvaluationOutcome, JoinedEvaluationRecord


def calculate_metrics(records: list[JoinedEvaluationRecord]) -> dict[str, float]:
    """Calculate core IDS evaluation metrics from joined evaluation records.

    Formulas:
    1. precision = TP / (TP + FP) (with zero-division guard returning 1.0 if TP + FP == 0).
    2. recall = TP / (TP + FN) (with zero-division guard returning 1.0 if TP + FN == 0).
    3. f1_score = 2 * precision * recall / (precision + recall) (0.0 if precision + recall == 0).
    4. false_positives_per_1000_events = (FP / total_events) * 1000.0 (0.0 if total_events == 0).
    5. explanation_correctness_rate = fraction (0.0 to 1.0) of TPs where
       alert['missing'] == label.missing_ops (0.0 if TP == 0).

    Args:
        records: List of JoinedEvaluationRecord objects produced by join_alerts_with_labels.

    Returns:
        Dictionary mapping metric names to float values.
    """
    if not isinstance(records, list):
        raise TypeError(f"records must be a list, got {type(records).__name__}.")

    tp = 0
    fp = 0
    fn = 0
    tn = 0
    explanation_matches = 0

    for rec in records:
        if not isinstance(rec, JoinedEvaluationRecord):
            raise TypeError(
                f"Each record must be a JoinedEvaluationRecord, got {type(rec).__name__}."
            )
        if rec.outcome == EvaluationOutcome.TP:
            tp += 1
            alert_missing = (
                list(rec.alert.get("missing", [])) if isinstance(rec.alert, dict) else []
            )
            label_missing = list(rec.label.missing_ops)
            if alert_missing == label_missing:
                explanation_matches += 1
        elif rec.outcome == EvaluationOutcome.FP:
            fp += 1
        elif rec.outcome == EvaluationOutcome.FN:
            fn += 1
        elif rec.outcome == EvaluationOutcome.TN:
            tn += 1

    total_events = len(records)

    precision = (tp / (tp + fp)) if (tp + fp) > 0 else 1.0
    recall = (tp / (tp + fn)) if (tp + fn) > 0 else 1.0
    f1_score = (
        (2.0 * precision * recall / (precision + recall)) if (precision + recall) > 0.0 else 0.0
    )
    false_positives_per_1000_events = (fp / total_events) * 1000.0 if total_events > 0 else 0.0
    explanation_correctness_rate = (explanation_matches / tp) if tp > 0 else 0.0

    return {
        "tp": float(tp),
        "fp": float(fp),
        "fn": float(fn),
        "tn": float(tn),
        "total_events": float(total_events),
        "precision": float(precision),
        "recall": float(recall),
        "f1_score": float(f1_score),
        "false_positives_per_1000_events": float(false_positives_per_1000_events),
        "explanation_correctness_rate": float(explanation_correctness_rate),
    }
