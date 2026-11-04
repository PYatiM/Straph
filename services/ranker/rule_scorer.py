"""Rule-severity heuristic priority scorer for Straph alerts.

Computes normalized priority scores in [0.0, 1.0] based on the approved deterministic
rule hierarchy and attempt vs acceptance severity.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "RULE_PRIORITY_SCORES",
    "RuleSeverityScorer",
]

RULE_PRIORITY_SCORES: dict[tuple[str, str], float] = {
    ("R0", "accepted"): 1.0,
    ("R0", "attempted"): 0.8,
    ("R1", "accepted"): 0.9,
    ("R1", "attempted"): 0.7,
    ("R2", "accepted"): 0.75,
    ("R2", "attempted"): 0.5,
    ("R3", "accepted"): 0.6,
    ("R3", "attempted"): 0.4,
    ("R4", "accepted"): 0.3,
    ("R4", "attempted"): 0.3,
}


class RuleSeverityScorer:
    """Computes deterministic priority scores in [0.0, 1.0] from rule and severity."""

    @staticmethod
    def score_alert(alert: dict[str, Any]) -> float:
        """Compute normalized priority score in [0.0, 1.0].

        Deterministic mapping:
        - R0 (Identity/BOLA) accepted: 1.0; attempted: 0.8
        - R1 (Replay) accepted: 0.9; attempted: 0.7
        - R2 (Skipped) accepted: 0.75; attempted: 0.5
        - R3 (Out of order) accepted: 0.6; attempted: 0.4
        - R4 (Unmodeled): 0.3
        """
        rule = str(alert.get("rule", "")).upper()
        severity = str(alert.get("severity", "attempted")).lower()

        if rule == "R4":
            return 0.3

        score = RULE_PRIORITY_SCORES.get((rule, severity))
        if score is not None:
            return score

        return 0.0
