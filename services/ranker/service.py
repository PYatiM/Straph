"""Alert scoring service applying GBDTRanker with immutable field protection."""

from __future__ import annotations

import copy
import json
import logging
from typing import Any

from services.detector.alert_store import SQLiteAlertStore
from services.ranker.features import extract_features
from services.ranker.gbdt_ranker import GBDTRanker
from services.ranker.rule_scorer import RuleSeverityScorer

log = logging.getLogger("straph.ranker.service")

__all__ = [
    "AlertScoringService",
]


class AlertScoringService:
    """Asynchronous and batch alert priority scoring service.

    Attaches ML priority score to detection alerts while strictly preserving
    all core detection and explanation fields bit-for-bit without mutation.
    """

    def __init__(
        self,
        ranker: GBDTRanker | None = None,
        alert_store: SQLiteAlertStore | None = None,
        clean_stats: dict[str, Any] | None = None,
    ) -> None:
        self.ranker: GBDTRanker | None = ranker
        self.alert_store: SQLiteAlertStore | None = alert_store
        self.clean_stats: dict[str, Any] = dict(clean_stats) if clean_stats is not None else {}

    def score_alert(
        self,
        alert: dict[str, Any],
        instance_history: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Score an individual alert, populating rank_score without mutating other fields.

        Args:
            alert: Raw alert dictionary matching contracts/alert.schema.json.
            instance_history: Optional history list for the affected instance.

        Returns:
            Alert dictionary with rank_score populated and all other fields identical.
        """
        # Create a deep copy to ensure original alert dict is immutable
        scored_alert = copy.deepcopy(alert)
        history = list(instance_history) if instance_history is not None else []

        # Extract features without label leakage
        features = extract_features(alert, history, self.clean_stats)

        # Compute priority score
        if self.ranker is not None and self.ranker.is_fitted:
            score = self.ranker.predict_rank(features)
        else:
            # Fallback to deterministic rule heuristic priority scorer
            score = RuleSeverityScorer.score_alert(alert)

        scored_alert["rank_score"] = float(score)

        # Verify bit-for-bit immutability on all original keys
        for key, orig_val in alert.items():
            if key == "rank_score":
                continue
            if scored_alert.get(key) != orig_val:
                raise RuntimeError(
                    f"Contract violation: Alert field '{key}' was mutated during ML scoring."
                )

        # Persist updated alert with rank_score if alert_store is configured
        if self.alert_store is not None:
            self.alert_store.save_alert(scored_alert)

        return scored_alert

    def score_alerts(
        self,
        alerts: list[dict[str, Any]],
        instance_histories: dict[str, list[dict[str, Any]]] | None = None,
    ) -> list[dict[str, Any]]:
        """Score a collection of alerts preserving field immutability."""
        scored_list: list[dict[str, Any]] = []
        histories = instance_histories or {}

        for alert in alerts:
            res_id = str(alert.get("resource", {}).get("id", ""))
            hist = histories.get(res_id, [])
            scored_list.append(self.score_alert(alert, hist))

        return scored_list

    def score_unranked_alerts(self, limit: int = 100) -> list[dict[str, Any]]:
        """Query and score unranked alerts (rank_score IS NULL) from the SQLite alert store."""
        if self.alert_store is None:
            raise RuntimeError("SQLiteAlertStore must be configured to score unranked alerts.")

        with self.alert_store._lock:
            cursor = self.alert_store._conn.execute(
                "SELECT raw_json FROM alerts WHERE rank_score IS NULL ORDER BY timestamp ASC LIMIT ?",
                (limit,),
            )
            rows = cursor.fetchall()

        unranked_alerts = [json.loads(row[0]) for row in rows]
        scored_alerts: list[dict[str, Any]] = []

        for alert in unranked_alerts:
            scored = self.score_alert(alert)
            scored_alerts.append(scored)

        return scored_alerts

    def process_raw_message(self, message: bytes | str | dict[str, Any]) -> dict[str, Any]:
        """Deserialize raw message from straph.alerts and attach rank_score."""
        if isinstance(message, (bytes, bytearray)):
            payload: dict[str, Any] = json.loads(message.decode("utf-8"))
        elif isinstance(message, str):
            payload = json.loads(message)
        elif isinstance(message, dict):
            payload = dict(message)
        else:
            raise TypeError(f"Unsupported message type: {type(message).__name__}")

        return self.score_alert(payload)
