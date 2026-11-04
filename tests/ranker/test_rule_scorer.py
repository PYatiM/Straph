"""Unit tests for RuleSeverityScorer heuristic priority scoring."""

from __future__ import annotations

import pytest

from services.ranker.rule_scorer import RuleSeverityScorer


@pytest.mark.parametrize(
    ("rule", "severity", "expected_score"),
    [
        ("R0", "accepted", 1.0),
        ("R0", "attempted", 0.8),
        ("R1", "accepted", 0.9),
        ("R1", "attempted", 0.7),
        ("R2", "accepted", 0.75),
        ("R2", "attempted", 0.5),
        ("R3", "accepted", 0.6),
        ("R3", "attempted", 0.4),
        ("R4", "accepted", 0.3),
        ("R4", "attempted", 0.3),
        ("UNKNOWN", "accepted", 0.0),
    ],
)
def test_rule_severity_exact_scores(rule: str, severity: str, expected_score: float) -> None:
    """Assert exact priority score per rule and severity permutation."""
    alert = {"rule": rule, "severity": severity}
    assert RuleSeverityScorer.score_alert(alert) == expected_score


def test_monotonic_ordering_per_rule() -> None:
    """Within rules R0-R3, accepted severity must score strictly higher than attempted."""
    for rule in ["R0", "R1", "R2", "R3"]:
        accepted = RuleSeverityScorer.score_alert({"rule": rule, "severity": "accepted"})
        attempted = RuleSeverityScorer.score_alert({"rule": rule, "severity": "attempted"})
        assert accepted > attempted, (
            f"Rule {rule}: accepted ({accepted}) <= attempted ({attempted})"
        )


def test_monotonic_ordering_across_rules_accepted() -> None:
    """Across accepted severity, rules must strictly follow R0 > R1 > R2 > R3 > R4."""
    r0 = RuleSeverityScorer.score_alert({"rule": "R0", "severity": "accepted"})
    r1 = RuleSeverityScorer.score_alert({"rule": "R1", "severity": "accepted"})
    r2 = RuleSeverityScorer.score_alert({"rule": "R2", "severity": "accepted"})
    r3 = RuleSeverityScorer.score_alert({"rule": "R3", "severity": "accepted"})
    r4 = RuleSeverityScorer.score_alert({"rule": "R4", "severity": "accepted"})

    assert r0 > r1 > r2 > r3 > r4
    assert (r0, r1, r2, r3, r4) == (1.0, 0.9, 0.75, 0.6, 0.3)


def test_monotonic_ordering_across_rules_attempted() -> None:
    """Across attempted severity, rules must strictly follow R0 > R1 > R2 > R3 > R4."""
    r0 = RuleSeverityScorer.score_alert({"rule": "R0", "severity": "attempted"})
    r1 = RuleSeverityScorer.score_alert({"rule": "R1", "severity": "attempted"})
    r2 = RuleSeverityScorer.score_alert({"rule": "R2", "severity": "attempted"})
    r3 = RuleSeverityScorer.score_alert({"rule": "R3", "severity": "attempted"})
    r4 = RuleSeverityScorer.score_alert({"rule": "R4", "severity": "attempted"})

    assert r0 > r1 > r2 > r3 > r4
    assert (r0, r1, r2, r3, r4) == (0.8, 0.7, 0.5, 0.4, 0.3)


def test_score_bounds_and_case_insensitivity() -> None:
    """Scores must stay in [0.0, 1.0] and handle lower/uppercase rule keys gracefully."""
    for r in ["r0", "R0", "r1", "R1", "r2", "R2", "r3", "R3", "r4", "R4"]:
        for s in ["accepted", "ACCEPTED", "attempted", "ATTEMPTED"]:
            score = RuleSeverityScorer.score_alert({"rule": r, "severity": s})
            assert 0.0 <= score <= 1.0
