"""SQLite repository for persistent alert storage and querying."""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any


class SQLiteAlertStore:
    """Thread-safe SQLite repository for alerts conforming to contracts/alert.schema.json."""

    def __init__(self, db_path: str | Path = ":memory:") -> None:
        self.db_path = str(db_path)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._init_db()

    def _init_db(self) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS alerts (
                    alert_id TEXT PRIMARY KEY,
                    event_id TEXT,
                    timestamp TEXT,
                    label TEXT,
                    rule TEXT,
                    severity TEXT,
                    resource_type TEXT,
                    resource_id TEXT,
                    subject_sub TEXT,
                    owasp_class TEXT,
                    rank_score REAL,
                    raw_json TEXT,
                    alert_json TEXT
                )
                """
            )
            cursor = self._conn.execute("PRAGMA table_info(alerts)")
            cols = {row[1] for row in cursor.fetchall()}
            if "raw_json" not in cols:
                self._conn.execute("ALTER TABLE alerts ADD COLUMN raw_json TEXT")
            if "alert_json" not in cols:
                self._conn.execute("ALTER TABLE alerts ADD COLUMN alert_json TEXT")
            if "subject_sub" not in cols:
                self._conn.execute("ALTER TABLE alerts ADD COLUMN subject_sub TEXT")
            if "owasp_class" not in cols:
                self._conn.execute("ALTER TABLE alerts ADD COLUMN owasp_class TEXT")
            self._conn.execute("CREATE INDEX IF NOT EXISTS idx_alerts_label ON alerts(label)")
            self._conn.execute("CREATE INDEX IF NOT EXISTS idx_alerts_rule ON alerts(rule)")
            self._conn.execute("CREATE INDEX IF NOT EXISTS idx_alerts_rank ON alerts(rank_score)")

    def save_alert(self, alert_dict: dict[str, Any]) -> None:
        """Persist alert dictionary to SQLite with JSON serialization."""
        alert_id = str(alert_dict["alert_id"])
        event_id = str(alert_dict.get("event_id", ""))
        timestamp = str(alert_dict.get("timestamp", ""))
        label = str(alert_dict.get("label", ""))
        rule = str(alert_dict.get("rule", ""))
        severity = str(alert_dict.get("severity", ""))

        resource = alert_dict.get("resource", {})
        resource_type = str(resource.get("type", ""))
        resource_id = str(resource.get("id", ""))

        subject = alert_dict.get("subject", {})
        subject_sub = str(subject.get("sub") or subject.get("identity") or "")

        owasp_class = str(alert_dict.get("owasp_class", ""))
        rank_score = alert_dict.get("rank_score")
        if rank_score is not None:
            rank_score = float(rank_score)

        raw_json = json.dumps(alert_dict)

        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO alerts (
                    alert_id, event_id, timestamp, label, rule, severity,
                    resource_type, resource_id, subject_sub, owasp_class,
                    rank_score, raw_json, alert_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(alert_id) DO UPDATE SET
                    rank_score = excluded.rank_score,
                    raw_json = excluded.raw_json,
                    alert_json = excluded.alert_json
                """,
                (
                    alert_id,
                    event_id,
                    timestamp,
                    label,
                    rule,
                    severity,
                    resource_type,
                    resource_id,
                    subject_sub,
                    owasp_class,
                    rank_score,
                    raw_json,
                    raw_json,
                ),
            )

    def get_alert(self, alert_id: str) -> dict[str, Any] | None:
        """Retrieve and deserialize alert by alert_id."""
        with self._lock:
            cursor = self._conn.execute(
                "SELECT COALESCE(raw_json, alert_json) FROM alerts WHERE alert_id = ?",
                (alert_id,),
            )
            row = cursor.fetchone()
            if row is None or row[0] is None:
                return None
            return json.loads(row[0])

    def query_alerts(
        self,
        label: str | None = None,
        rule: str | None = None,
        min_rank: float | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Query alerts by label, rule, or min_rank, returning deserialized dictionaries."""
        query = "SELECT COALESCE(raw_json, alert_json) FROM alerts WHERE 1=1"
        params: list[Any] = []

        if label is not None:
            query += " AND label = ?"
            params.append(label)

        if rule is not None:
            query += " AND rule = ?"
            params.append(rule)

        if min_rank is not None:
            query += " AND rank_score IS NOT NULL AND rank_score >= ?"
            params.append(min_rank)

        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)

        with self._lock:
            cursor = self._conn.execute(query, params)
            rows = cursor.fetchall()
            return [json.loads(row[0]) for row in rows if row[0] is not None]

    def get_alerts(
        self,
        label: str | None = None,
        rule: str | None = None,
        severity: str | None = None,
        min_rank: float | None = None,
        limit: int = 50,
        resource_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Retrieve alerts filtered by criteria and ordered by priority and time."""
        query = "SELECT COALESCE(raw_json, alert_json) FROM alerts WHERE 1=1"
        params: list[Any] = []

        if label is not None:
            query += " AND label = ?"
            params.append(str(label))

        if rule is not None:
            query += " AND rule = ?"
            params.append(str(rule))

        if severity is not None:
            query += " AND severity = ?"
            params.append(str(severity))

        if min_rank is not None:
            query += " AND rank_score IS NOT NULL AND rank_score >= ?"
            params.append(float(min_rank))

        if resource_id is not None:
            query += " AND resource_id = ?"
            params.append(str(resource_id))

        query += " ORDER BY rank_score DESC, timestamp DESC LIMIT ?"
        params.append(int(limit))

        with self._lock:
            cursor = self._conn.execute(query, params)
            rows = cursor.fetchall()
            return [json.loads(row[0]) for row in rows if row[0] is not None]

    def clear(self) -> None:
        """Clear all stored alerts."""
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM alerts")

    def count(self) -> int:
        """Return total alerts stored."""
        with self._lock:
            cursor = self._conn.execute("SELECT COUNT(*) FROM alerts")
            return int(cursor.fetchone()[0])

    def close(self) -> None:
        """Close database connection."""
        with self._lock:
            self._conn.close()
