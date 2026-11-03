"""SQLite repository for persistent alert storage and querying."""

from __future__ import annotations

import json
import sqlite3
import threading
from typing import Any


class SQLiteAlertStore:
    """Thread-safe SQLite repository for alerts conforming to contracts/alert.schema.json."""

    def __init__(self, db_path: str = ":memory:") -> None:
        self.db_path = db_path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
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
                    raw_json TEXT
                )
                """
            )
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
                    rank_score, raw_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(alert_id) DO UPDATE SET
                    rank_score = excluded.rank_score,
                    raw_json = excluded.raw_json
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
                ),
            )

    def get_alert(self, alert_id: str) -> dict[str, Any] | None:
        """Retrieve and deserialize alert by alert_id."""
        with self._lock:
            cursor = self._conn.execute(
                "SELECT raw_json FROM alerts WHERE alert_id = ?",
                (alert_id,),
            )
            row = cursor.fetchone()
            if row is None:
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
        query = "SELECT raw_json FROM alerts WHERE 1=1"
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
            return [json.loads(row[0]) for row in rows]

    def count(self) -> int:
        """Return total alerts stored."""
        with self._lock:
            cursor = self._conn.execute("SELECT COUNT(*) FROM alerts")
            return int(cursor.fetchone()[0])

    def close(self) -> None:
        """Close database connection."""
        with self._lock:
            self._conn.close()
