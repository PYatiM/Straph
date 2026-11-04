"""Unit tests for AlertFilterEngine multi-criteria filtering, ordering, and pagination."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

import pytest

from services.dashboard.filters import AlertFilterEngine


@pytest.fixture
def db_conn() -> sqlite3.Connection:
    """Create in-memory SQLite connection populated with diverse alerts."""
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE alerts (
            alert_id TEXT PRIMARY KEY,
            event_id TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            label TEXT NOT NULL,
            rule TEXT NOT NULL,
            severity TEXT NOT NULL,
            resource_type TEXT NOT NULL,
            resource_id TEXT NOT NULL,
            rank_score REAL,
            alert_json TEXT NOT NULL
        )
        """
    )

    test_data: list[dict[str, Any]] = [
        # (id, timestamp, label, rule, severity, rank_score)
        {
            "id": "A1",
            "ts": "2026-11-04T10:00:00Z",
            "lbl": "SKIPPED",
            "r": "R2",
            "s": "accepted",
            "rank": 0.9,
        },
        {
            "id": "A2",
            "ts": "2026-11-04T11:00:00Z",
            "lbl": "OUT_OF_ORDER",
            "r": "R3",
            "s": "accepted",
            "rank": 0.7,
        },
        {
            "id": "A3",
            "ts": "2026-11-04T12:00:00Z",
            "lbl": "OUT_OF_ORDER",
            "r": "R3",
            "s": "attempted",
            "rank": 0.7,
        },
        {
            "id": "A4",
            "ts": "2026-11-04T09:00:00Z",
            "lbl": "REPLAYED",
            "r": "R1",
            "s": "accepted",
            "rank": 0.4,
        },
        {
            "id": "A5",
            "ts": "2026-11-04T08:00:00Z",
            "lbl": "UNMODELED",
            "r": "R4",
            "s": "attempted",
            "rank": 0.2,
        },
    ]

    for item in test_data:
        payload = {
            "alert_id": item["id"],
            "event_id": f"event-{item['id']}",
            "timestamp": item["ts"],
            "label": item["lbl"],
            "rule": item["r"],
            "severity": item["s"],
            "rank_score": item["rank"],
            "resource": {"type": "Order", "id": "1001"},
        }
        conn.execute(
            """
            INSERT INTO alerts (
                alert_id, event_id, timestamp, label, rule, severity,
                resource_type, resource_id, rank_score, alert_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item["id"],
                f"event-{item['id']}",
                item["ts"],
                item["lbl"],
                item["r"],
                item["s"],
                "Order",
                "1001",
                item["rank"],
                json.dumps(payload),
            ),
        )
    conn.commit()
    return conn


def test_sorting_rank_score_and_secondary_timestamp(db_conn: sqlite3.Connection) -> None:
    """Alerts must sort by rank_score DESC; equal ranks sort by timestamp DESC."""
    alerts, total = AlertFilterEngine.apply_filters(db_conn)
    assert total == 5
    assert len(alerts) == 5

    alert_ids = [a["alert_id"] for a in alerts]
    # A1 (0.9), A3 (0.7, ts=12:00), A2 (0.7, ts=11:00), A4 (0.4), A5 (0.2)
    assert alert_ids == ["A1", "A3", "A2", "A4", "A5"]


def test_page_slicing_and_pagination(db_conn: sqlite3.Connection) -> None:
    """Pagination offsets and limits must slice result sets while preserving total count."""
    # Page 1: limit 2, offset 0
    page1, total1 = AlertFilterEngine.apply_filters(db_conn, offset=0, limit=2)
    assert total1 == 5
    assert len(page1) == 2
    assert [a["alert_id"] for a in page1] == ["A1", "A3"]

    # Page 2: limit 2, offset 2
    page2, total2 = AlertFilterEngine.apply_filters(db_conn, offset=2, limit=2)
    assert total2 == 5
    assert len(page2) == 2
    assert [a["alert_id"] for a in page2] == ["A2", "A4"]

    # Page 3: limit 2, offset 4
    page3, total3 = AlertFilterEngine.apply_filters(db_conn, offset=4, limit=2)
    assert total3 == 5
    assert len(page3) == 1
    assert [a["alert_id"] for a in page3] == ["A5"]


def test_filter_by_rule_and_label(db_conn: sqlite3.Connection) -> None:
    """Filter by rule and label returns matching subset and exact count."""
    alerts, total = AlertFilterEngine.apply_filters(db_conn, rule="R3")
    assert total == 2
    assert len(alerts) == 2
    for a in alerts:
        assert a["rule"] == "R3"

    alerts_label, total_label = AlertFilterEngine.apply_filters(db_conn, label="SKIPPED")
    assert total_label == 1
    assert alerts_label[0]["alert_id"] == "A1"


def test_filter_by_min_rank(db_conn: sqlite3.Connection) -> None:
    """Filter by min_rank returns alerts with rank_score >= threshold."""
    alerts, total = AlertFilterEngine.apply_filters(db_conn, min_rank=0.7)
    assert total == 3
    assert len(alerts) == 3
    assert [a["alert_id"] for a in alerts] == ["A1", "A3", "A2"]


def test_combined_filters(db_conn: sqlite3.Connection) -> None:
    """Combined rule, severity, and min_rank filters work conjunctively."""
    alerts, total = AlertFilterEngine.apply_filters(
        db_conn,
        rule="R3",
        severity="accepted",
        min_rank=0.5,
    )
    assert total == 1
    assert alerts[0]["alert_id"] == "A2"


def test_raw_json_column_compatibility() -> None:
    """AlertFilterEngine transparently supports tables using raw_json column."""
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE alerts (
            alert_id TEXT PRIMARY KEY,
            timestamp TEXT,
            label TEXT,
            rule TEXT,
            severity TEXT,
            rank_score REAL,
            raw_json TEXT
        )
        """
    )
    payload = {"alert_id": "RAW-1", "rule": "R0", "rank_score": 1.0}
    conn.execute(
        "INSERT INTO alerts VALUES ('RAW-1', '2026-11-04T10:00:00Z', 'IDENTITY_MISMATCH', 'R0', 'accepted', 1.0, ?)",
        (json.dumps(payload),),
    )
    conn.commit()

    alerts, total = AlertFilterEngine.apply_filters(conn, rule="R0")
    assert total == 1
    assert alerts[0]["alert_id"] == "RAW-1"
