"""Ground-truth label joiner and confusion-matrix outcome aligner for evaluation.

Joins detector alerts against GroundTruthLabel records by event_id and classifies
each event into True Positive (TP), False Positive (FP), False Negative (FN), or
True Negative (TN).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from services.evaluation.labels import GroundTruthLabel


class EvaluationOutcome(StrEnum):
    """Confusion matrix classification outcome for a single API event."""

    TP = "TP"
    FP = "FP"
    FN = "FN"
    TN = "TN"


@dataclass(slots=True)
class JoinedEvaluationRecord:
    """Joined evaluation record pairing a ground-truth label with any detector alert."""

    event_id: str
    label: GroundTruthLabel
    alert: dict[str, Any] | None
    outcome: EvaluationOutcome
    rule_matched: bool = False
    label_matched: bool = False
    explanation_matched: bool = False

    @property
    def is_tp(self) -> bool:
        """Return True if outcome is a True Positive."""
        return self.outcome == EvaluationOutcome.TP

    @property
    def is_fp(self) -> bool:
        """Return True if outcome is a False Positive."""
        return self.outcome == EvaluationOutcome.FP

    @property
    def is_fn(self) -> bool:
        """Return True if outcome is a False Negative."""
        return self.outcome == EvaluationOutcome.FN

    @property
    def is_tn(self) -> bool:
        """Return True if outcome is a True Negative."""
        return self.outcome == EvaluationOutcome.TN


def join_alerts_with_labels(
    alerts: list[dict[str, Any]],
    labels: list[GroundTruthLabel],
    *,
    strict_rule_match: bool = False,
) -> list[JoinedEvaluationRecord]:
    """Match detector alerts to ground-truth labels on event_id and classify outcomes.

    Classification rules per event:
    - True Positive (TP): alert raised and label.is_violation == True
      (when strict_rule_match=True, also requires matching rule or label; by default
      any alert on a violation event is a detection TP while rule_matched and
      label_matched track exact taxonomy agreement, or if alert carries a mismatched
      non-baseline rule when strict_rule_match=True).
    - False Positive (FP): alert raised but label.is_violation == False.
    - False Negative (FN): label.is_violation == True but no alert raised.
    - True Negative (TN): no alert raised and label.is_violation == False.

    Args:
        alerts: List of alert dictionaries emitted by a detector.
        labels: List of GroundTruthLabel records for the replayed event stream.
        strict_rule_match: If True, require alert rule/label to match label.expected_rule
            or label.expected_label for a TP. Defaults to False so sequence baselines
            that emit generic rules (e.g. R2/R3) can be evaluated alongside Straph while
            still recording rule_matched and label_matched.

    Returns:
        List of JoinedEvaluationRecord in the same order as labels.
    """
    if not isinstance(alerts, list):
        raise TypeError(f"alerts must be a list, got {type(alerts).__name__}.")
    if not isinstance(labels, list):
        raise TypeError(f"labels must be a list, got {type(labels).__name__}.")

    alert_by_event_id: dict[str, dict[str, Any]] = {}
    for alert in alerts:
        if not isinstance(alert, dict):
            raise TypeError(f"Each alert must be a dict, got {type(alert).__name__}.")
        raw_id = str(alert.get("event_id", "")).strip().lower()
        if raw_id and raw_id not in alert_by_event_id:
            alert_by_event_id[raw_id] = alert

    records: list[JoinedEvaluationRecord] = []

    for gt in labels:
        model_gt = gt if isinstance(gt, GroundTruthLabel) else GroundTruthLabel.model_validate(gt)
        ev_id = model_gt.event_id.strip().lower()
        matched_alert = alert_by_event_id.get(ev_id)

        if matched_alert is not None:
            alert_label = str(matched_alert.get("label", ""))
            alert_rule = str(matched_alert.get("rule", ""))
            alert_missing = list(matched_alert.get("missing", []))

            expected_label_str = str(model_gt.expected_label)
            expected_rule_str = (
                str(model_gt.expected_rule) if model_gt.expected_rule is not None else ""
            )

            label_matched = bool(alert_label and alert_label == expected_label_str)
            rule_matched = bool(
                not expected_rule_str or (alert_rule and alert_rule == expected_rule_str)
            )
            explanation_matched = alert_missing == list(model_gt.missing_ops)

            if model_gt.is_violation:
                if strict_rule_match and not (label_matched or rule_matched):
                    outcome = EvaluationOutcome.FN
                else:
                    outcome = EvaluationOutcome.TP
            else:
                outcome = EvaluationOutcome.FP
                label_matched = False
                rule_matched = False
                explanation_matched = False
        else:
            label_matched = False
            rule_matched = False
            explanation_matched = False
            if model_gt.is_violation:
                outcome = EvaluationOutcome.FN
            else:
                outcome = EvaluationOutcome.TN

        records.append(
            JoinedEvaluationRecord(
                event_id=model_gt.event_id,
                label=model_gt,
                alert=matched_alert,
                outcome=outcome,
                rule_matched=rule_matched,
                label_matched=label_matched,
                explanation_matched=explanation_matched,
            )
        )

    return records
