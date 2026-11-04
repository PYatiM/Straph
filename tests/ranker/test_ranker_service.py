"""Unit tests for AlertScoringService immutable field protection and scoring."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from services.detector.alert_store import SQLiteAlertStore
from services.ranker.gbdt_ranker import GBDTRanker
from services.ranker.service import AlertScoringService
from tests.detector.fixtures.tc2_skipped_payment import get_tc2_expected_alerts

ROOT: Path = Path(__file__).resolve().parents[2]
ALERT_SCHEMA_PATH: Path = ROOT / "contracts" / "alert.schema.json"


@pytest.fixture
def alert_validator() -> Draft202012Validator:
    """Validator for alert.schema.json."""
    schema = json.loads(ALERT_SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


@pytest.fixture
def trained_ranker() -> GBDTRanker:
    """Provide a trained GBDTRanker instance."""
    X = [
        {
            "rule_severity_code": 4.0,
            "is_accepted": 1.0,
            "has_role_mismatch": 1.0,
            "missing_steps_count": 2.0,
            "history_length": 5.0,
            "transition_rarity": 0.8,
        },
        {
            "rule_severity_code": 0.5,
            "is_accepted": 0.0,
            "has_role_mismatch": 0.0,
            "missing_steps_count": 0.0,
            "history_length": 1.0,
            "transition_rarity": 0.0,
        },
    ] * 20
    y = [1, 0] * 20
    ranker = GBDTRanker()
    ranker.train(X, y)
    return ranker


def test_immutable_field_protection_with_trained_ranker(
    trained_ranker: GBDTRanker,
    alert_validator: Draft202012Validator,
) -> None:
    """Ensure all core detection fields remain bit-for-bit identical after scoring."""
    raw_alerts = get_tc2_expected_alerts()
    assert len(raw_alerts) >= 1
    original_alert = raw_alerts[0]

    original_snapshot = copy.deepcopy(original_alert)

    service = AlertScoringService(ranker=trained_ranker)
    scored_alert = service.score_alert(original_alert)

    # 1. Input dictionary itself must remain unchanged
    assert original_alert == original_snapshot

    # 2. rank_score must be populated in [0.0, 1.0]
    assert "rank_score" in scored_alert
    assert isinstance(scored_alert["rank_score"], float)
    assert 0.0 <= scored_alert["rank_score"] <= 1.0

    # 3. Every other field must match snapshot exactly
    protected_keys = [
        "alert_id",
        "event_id",
        "timestamp",
        "label",
        "rule",
        "severity",
        "subject",
        "resource",
        "expected_path",
        "observed",
        "missing",
        "owasp_class",
        "flags",
        "model_version",
    ]
    for key in protected_keys:
        assert scored_alert[key] == original_snapshot[key], f"Field '{key}' was altered!"

    # 4. Scored alert must strictly validate against alert.schema.json
    errors = list(alert_validator.iter_errors(scored_alert))
    assert errors == []


def test_scoring_fallback_to_heuristic_without_ranker() -> None:
    """Without trained ML model, service safely falls back to deterministic RuleSeverityScorer."""
    alert: dict[str, Any] = {
        "alert_id": "33333333-3333-4333-8333-333333333333",
        "event_id": "44444444-4444-4444-8444-444444444444",
        "timestamp": "2026-11-04T10:00:00Z",
        "label": "SKIPPED",
        "rule": "R2",
        "severity": "accepted",
        "subject": {"issuer": "auth", "sub": "u1", "role": "customer"},
        "resource": {"type": "Order", "id": "1001"},
        "expected_path": [],
        "observed": {"operation": "POST /fulfill", "state": "PAID", "status_code": 200},
        "missing": [],
        "owasp_class": "BLA6",
        "flags": {"tainted": False, "late": False, "retry": False},
        "model_version": "v1.0.0",
    }
    service = AlertScoringService(ranker=None)
    scored = service.score_alert(alert)

    # R2 accepted heuristic score is 0.75
    assert scored["rank_score"] == 0.75
    assert scored["rule"] == "R2"
    assert scored["severity"] == "accepted"


def test_score_unranked_alerts_in_sqlite(trained_ranker: GBDTRanker) -> None:
    """Service queries unranked alerts from SQLite store, attaches scores, and updates store."""
    store = SQLiteAlertStore(":memory:")
    alert = get_tc2_expected_alerts()[0]
    alert_copy = copy.deepcopy(alert)
    alert_copy.pop("rank_score", None)

    store.save_alert(alert_copy)
    assert store.count() == 1

    # Verify initially unranked in SQLite
    unranked_before = store.query_alerts(min_rank=0.0)
    assert unranked_before == []

    service = AlertScoringService(ranker=trained_ranker, alert_store=store)
    scored_alerts = service.score_unranked_alerts()

    assert len(scored_alerts) == 1
    assert scored_alerts[0]["rank_score"] is not None

    # Verify updated in SQLite
    stored = store.get_alert(alert["alert_id"])
    assert stored is not None
    assert stored["rank_score"] is not None
    assert stored["rule"] == alert["rule"]
    assert stored["label"] == alert["label"]


def test_process_raw_message_bytes_and_string(trained_ranker: GBDTRanker) -> None:
    """Service correctly ingests JSON bytes and strings from event bus."""
    alert = get_tc2_expected_alerts()[0]
    service = AlertScoringService(ranker=trained_ranker)

    # Ingest bytes
    raw_bytes = json.dumps(alert).encode("utf-8")
    scored_from_bytes = service.process_raw_message(raw_bytes)
    assert isinstance(scored_from_bytes["rank_score"], float)

    # Ingest str
    raw_str = json.dumps(alert)
    scored_from_str = service.process_raw_message(raw_str)
    assert isinstance(scored_from_str["rank_score"], float)
