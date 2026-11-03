"""SQLite-backed alert store persisting detection alerts."""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any


class SQLiteAlertStore:
    """Thread-safe SQLite storage for contract-compliant detection alerts."""

    def __init__(self, db_path: str | Path = ":memory:") -> None:
        self.db_path = str(db_path)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(
            self.db_path,
            check_same_thread=False,
        )
        self._conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self) -> None:
        with self._lock:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS alerts (
                    alert_id TEXT PRIMARY KEY,
                    event_id TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    label TEXT NOT NULL,
                    rule TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    resource_type TEXT NOT NULL,
                    resource_id TEXT NOT NULL,
                    alert_json TEXT NOT NULL
                )
                """
            )
            self._conn.commit()

    def save_alert(self, alert: dict[str, Any]) -> None:
        """Persist a single alert dict."""
        resource = alert.get("resource", {})
        res_type = resource.get("type", "")
        res_id = resource.get("id", "")

        with self._lock:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO alerts (
                    alert_id, event_id, timestamp, label, rule, severity,
                    resource_type, resource_id, alert_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(alert.get("alert_id")),
                    str(alert.get("event_id")),
                    str(alert.get("timestamp")),
                    str(alert.get("label")),
                    str(alert.get("rule")),
                    str(alert.get("severity")),
                    res_type,
                    res_id,
                    json.dumps(alert),
                ),
            )
            self._conn.commit()

    def get_alerts(
        self,
        resource_id: str | None = None,
        label: str | None = None,
    ) -> list[dict[str, Any]]:
        """Retrieve alerts optionally filtered by resource_id or label."""
        query = "SELECT alert_json FROM alerts WHERE 1=1"
        params: list[Any] = []

        if resource_id is not None:
            query += " AND resource_id = ?"
            params.append(str(resource_id))

        if label is not None:
            query += " AND label = ?"
            params.append(str(label))

        query += " ORDER BY timestamp ASC"

        with self._lock:
            cursor = self._conn.execute(query, params)
            rows = cursor.fetchall()

        return [json.loads(row["alert_json"]) for row in rows]

    def get_alert(self, alert_id: str) -> dict[str, Any] | None:
        """Retrieve a single alert by alert_id."""
        with self._lock:
            cursor = self._conn.execute(
                "SELECT alert_json FROM alerts WHERE alert_id = ?", (alert_id,)
            )
            row = cursor.fetchone()
        if row:
            return json.loads(row["alert_json"])
        return None

    def clear(self) -> None:
        """Clear all stored alerts."""
        with self._lock:
            self._conn.execute("DELETE FROM alerts")
            self._conn.commit()
