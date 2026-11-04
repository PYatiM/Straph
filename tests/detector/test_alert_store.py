"""Tests for SQLiteAlertStore persistence, deduplication, and querying."""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from services.detector.alert_store import SQLiteAlertStore


def _make_alert(
    rule: str,
    label: str,
    rank_score: float | None = None,
    alert_id: str | None = None,
) -> dict[str, Any]:
    return {
        "alert_id": alert_id or str(uuid.uuid4()),
        "event_id": str(uuid.uuid4()),
        "timestamp": "2026-10-29T10:00:00.000Z",
        "label": label,
        "rule": rule,
        "severity": "accepted",
        "subject": {"sub": "user-1", "role": "customer"},
        "resource": {"type": "Order", "id": "1001"},
        "expected_path": ["POST /orders"],
        "observed": {"operation": "POST /orders", "state": "CART_ACTIVE", "status_code": 200},
        "missing": [],
        "owasp_class": "BLA6",
        "flags": {"tainted": True, "late": False, "retry": False},
        "model_version": "checkout-v0",
        "rank_score": rank_score,
    }


@pytest.fixture
def store() -> SQLiteAlertStore:
    return SQLiteAlertStore(":memory:")


def test_save_and_get_alert(store: SQLiteAlertStore) -> None:
    """Verify persisting an alert and retrieving by alert_id."""
    alert = _make_alert("R2", "SKIPPED", rank_score=0.85)
    alert_id = alert["alert_id"]

    store.save_alert(alert)
    retrieved = store.get_alert(alert_id)

    assert retrieved is not None
    assert retrieved["alert_id"] == alert_id
    assert retrieved["label"] == "SKIPPED"
    assert retrieved["rule"] == "R2"
    assert retrieved["rank_score"] == 0.85
    assert retrieved["resource"] == {"type": "Order", "id": "1001"}


def test_deduplication_on_duplicate_alert_id(store: SQLiteAlertStore) -> None:
    """Verify duplicate alert_id does not cause primary key crash and updates record."""
    alert_id = str(uuid.uuid4())
    alert_v1 = _make_alert("R2", "SKIPPED", rank_score=0.5, alert_id=alert_id)
    alert_v2 = _make_alert("R2", "SKIPPED", rank_score=0.95, alert_id=alert_id)

    store.save_alert(alert_v1)
    assert store.count() == 1

    # Saving again with same alert_id
    store.save_alert(alert_v2)
    assert store.count() == 1

    updated = store.get_alert(alert_id)
    assert updated is not None
    assert updated["rank_score"] == 0.95


def test_multi_filter_querying(store: SQLiteAlertStore) -> None:
    """Verify querying by label, rule, and min_rank filters."""
    a1 = _make_alert("R0", "IDENTITY_MISMATCH", rank_score=0.4)
    a2 = _make_alert("R2", "SKIPPED", rank_score=0.8)
    a3 = _make_alert("R2", "SKIPPED", rank_score=0.95)
    a4 = _make_alert("R3", "OUT_OF_ORDER", rank_score=0.2)

    for a in [a1, a2, a3, a4]:
        store.save_alert(a)

    assert store.count() == 4

    # Query by label
    r_skipped = store.query_alerts(label="SKIPPED")
    assert len(r_skipped) == 2
    assert all(a["label"] == "SKIPPED" for a in r_skipped)

    # Query by rule
    r_r0 = store.query_alerts(rule="R0")
    assert len(r_r0) == 1
    assert r_r0[0]["label"] == "IDENTITY_MISMATCH"

    # Query by min_rank
    r_ranked = store.query_alerts(min_rank=0.75)
    assert len(r_ranked) == 2
    assert {a["rank_score"] for a in r_ranked} == {0.8, 0.95}

    # Combined query: rule="R2" and min_rank=0.9
    r_combined = store.query_alerts(rule="R2", min_rank=0.9)
    assert len(r_combined) == 1
    assert r_combined[0]["rank_score"] == 0.95

    # Non-existent alert
    assert store.get_alert("non-existent-id") is None
