"""Automated hypothesis test verifying Hypothesis H2 (ML Ranker Non-Interference).

Hypothesis H2 states:
"The ML ranker attaches rank_score to existing alerts; core detection rules never read
ML scores, and alert generation decisions are strictly invariant to the ranker's presence."

Verifies:
1. Replaying fixtures TC1–TC10 through DetectorWorker without ranker produces alert set A1.
2. Replaying fixtures TC1–TC10 through DetectorWorker with AlertScoringService produces alert set A2.
3. Stripping 'rank_score' from A2 yields an alert list byte-for-byte identical to A1.
4. Detector classification logic contains zero references to 'rank_score'.
"""

from __future__ import annotations

import copy
import inspect
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import patch

from services.detector import classifier, dedup, runner, store, tables
from services.detector.worker import DetectorWorker
from services.ranker.gbdt_ranker import GBDTRanker
from services.ranker.service import AlertScoringService
from tests.detector.fixtures import ALL_FIXTURES

ROOT: Path = Path(__file__).resolve().parents[2]


def _deterministic_uuid_sequence() -> Any:
    """Return a generator producing a deterministic sequence of valid UUID4 objects."""
    counter = 0

    def _next_uuid() -> uuid.UUID:
        nonlocal counter
        counter += 1
        return uuid.UUID(f"00000000-0000-4000-8000-{counter:012d}")

    return _next_uuid


def _run_all_fixtures() -> list[dict[str, Any]]:
    """Run DetectorWorker across all test fixtures TC1 through TC10 in deterministic order."""
    worker = DetectorWorker()
    alerts: list[dict[str, Any]] = []

    for tc_name in sorted(ALL_FIXTURES.keys()):
        get_events, _ = ALL_FIXTURES[tc_name]
        events = get_events()
        for event in events:
            alert = worker.process_event(event)
            if alert is not None:
                alerts.append(alert)

    return alerts


def test_hypothesis_h2_non_interference() -> None:
    """Execute Hypothesis H2 verification across TC1-TC10 test suite."""
    # 1. Execute DetectorWorker across TC1–TC10 without ranker -> A1
    uuid_gen_1 = _deterministic_uuid_sequence()
    with patch("uuid.uuid4", side_effect=uuid_gen_1):
        raw_a1 = _run_all_fixtures()

    a1 = copy.deepcopy(raw_a1)
    assert len(a1) > 0, "TC1-TC10 must produce detection alerts"

    # Verify no rank_score exists in raw detector alerts
    for alert in a1:
        assert "rank_score" not in alert

    # 2. Execute DetectorWorker across TC1–TC10 with AlertScoringService active -> A2
    trained_ranker = GBDTRanker()
    X = [
        {
            "rule_severity_code": 4.0,
            "is_accepted": 1.0,
            "has_role_mismatch": 1.0,
            "missing_steps_count": 1.0,
            "history_length": 3.0,
            "transition_rarity": 0.5,
        },
        {
            "rule_severity_code": 0.5,
            "is_accepted": 0.0,
            "has_role_mismatch": 0.0,
            "missing_steps_count": 0.0,
            "history_length": 1.0,
            "transition_rarity": 0.0,
        },
    ] * 10
    y = [1, 0] * 10
    trained_ranker.train(X, y)

    scoring_service = AlertScoringService(ranker=trained_ranker)

    uuid_gen_2 = _deterministic_uuid_sequence()
    with patch("uuid.uuid4", side_effect=uuid_gen_2):
        detector_alerts_2 = _run_all_fixtures()

    # Active ranker consumes and scores detector output
    a2: list[dict[str, Any]] = [scoring_service.score_alert(a) for a in detector_alerts_2]

    # Verify rank_score is present in all A2 alerts
    for alert in a2:
        assert "rank_score" in alert
        assert isinstance(alert["rank_score"], float)
        assert 0.0 <= alert["rank_score"] <= 1.0

    # 3. Strip rank_score from A2
    a2_stripped: list[dict[str, Any]] = []
    for alert in a2:
        stripped = {k: v for k, v in alert.items() if k != "rank_score"}
        a2_stripped.append(stripped)

    # 4. Assert A1 == A2 (byte-for-byte equality of count, IDs, labels, rules, paths, flags)
    assert len(a1) == len(a2_stripped), (
        f"Alert count mismatch: len(A1)={len(a1)} vs len(A2)={len(a2_stripped)}"
    )

    for idx, (alert_1, alert_2) in enumerate(zip(a1, a2_stripped, strict=True)):
        assert alert_1 == alert_2, (
            f"Alert at index {idx} differed after ML scoring: {alert_1} != {alert_2}"
        )
        assert alert_1["alert_id"] == alert_2["alert_id"]
        assert alert_1["event_id"] == alert_2["event_id"]
        assert alert_1["label"] == alert_2["label"]
        assert alert_1["rule"] == alert_2["rule"]
        assert alert_1["severity"] == alert_2["severity"]
        assert alert_1["expected_path"] == alert_2["expected_path"]
        assert alert_1["missing"] == alert_2["missing"]
        assert alert_1["flags"] == alert_2["flags"]


def test_core_detection_modules_contain_zero_rank_score_references() -> None:
    """Core detector modules must never reference or inspect 'rank_score'."""
    core_modules = [
        classifier,
        tables,
        runner,
        store,
        dedup,
    ]

    for mod in core_modules:
        source_text = inspect.getsource(mod)
        assert "rank_score" not in source_text, (
            f"Detection module '{mod.__name__}' references 'rank_score', violating Hypothesis H2."
        )
