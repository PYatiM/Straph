"""Multi-criteria alert filtering and pagination engine for SQLite."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

__all__ = [
    "AlertFilterEngine",
]


class AlertFilterEngine:
    """Engine executing parameterized SQL filtering, ordering, and pagination on alert records."""

    @staticmethod
    def _get_json_column(conn: sqlite3.Connection) -> str:
        """Determine whether alerts table uses alert_json or raw_json column."""
        cursor = conn.execute("PRAGMA table_info(alerts)")
        columns = {row[1] for row in cursor.fetchall()}
        return "alert_json" if "alert_json" in columns else "raw_json"

    @classmethod
    def apply_filters(
        cls,
        conn: sqlite3.Connection,
        label: str | None = None,
        rule: str | None = None,
        min_rank: float | None = None,
        offset: int = 0,
        limit: int = 50,
        severity: str | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        """Apply combined WHERE clauses, return paginated results and total matching count.

        Sort order:
            Primary: rank_score DESC (NULLs last)
            Secondary: timestamp DESC

        Args:
            conn: SQLite connection object.
            label: Optional violation label filter.
            rule: Optional rule ID filter.
            min_rank: Minimum priority score threshold.
            offset: Number of records to skip.
            limit: Maximum records to return.
            severity: Optional severity filter.

        Returns:
            tuple[list[dict[str, Any]], int]: (paginated_alerts, total_count)
        """
        where_clauses: list[str] = ["1=1"]
        params: list[Any] = []

        if label is not None:
            where_clauses.append("label = ?")
            params.append(str(label))

        if rule is not None:
            where_clauses.append("rule = ?")
            params.append(str(rule))

        if severity is not None:
            where_clauses.append("severity = ?")
            params.append(str(severity))

        if min_rank is not None:
            where_clauses.append("rank_score IS NOT NULL AND rank_score >= ?")
            params.append(float(min_rank))

        where_sql = " AND ".join(where_clauses)

        # 1. Compute total matching count for pagination
        count_query = f"SELECT COUNT(*) FROM alerts WHERE {where_sql}"
        count_cursor = conn.execute(count_query, params)
        total_count = int(count_cursor.fetchone()[0])

        # 2. Query paginated results with primary rank_score DESC, secondary timestamp DESC
        json_col = cls._get_json_column(conn)
        select_query = (
            f"SELECT {json_col} FROM alerts WHERE {where_sql} "
            "ORDER BY rank_score DESC, timestamp DESC "
            "LIMIT ? OFFSET ?"
        )
        paginated_params = list(params) + [int(limit), int(offset)]

        cursor = conn.execute(select_query, paginated_params)
        rows = cursor.fetchall()
        alerts = [json.loads(row[0]) for row in rows]

        return alerts, total_count
