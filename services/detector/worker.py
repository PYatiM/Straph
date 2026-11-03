"""Detector worker evaluating events against reference workflow models."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from services.detector.alert_store import SQLiteAlertStore
from services.detector.alerts import AlertBuilder
from services.detector.classifier import classify
from services.detector.dedup import EventDeduplicator
from services.detector.store import InMemoryInstanceStore
from services.detector.tables import ModelTables, load_tables

log = logging.getLogger("straph.detector.worker")

ROOT_DIR = Path(__file__).resolve().parents[2]
CHECKOUT_MODEL_PATH = ROOT_DIR / "contracts" / "examples" / "model_checkout.json"


class DetectorWorker:
    """Deterministic IDS engine evaluating API events against reference workflow models."""

    def __init__(
        self,
        model_dict: dict[str, Any] | None = None,
        alert_store: SQLiteAlertStore | None = None,
        instance_store: InMemoryInstanceStore | None = None,
        deduplicator: EventDeduplicator | None = None,
        alert_builder: AlertBuilder | None = None,
    ) -> None:
        if model_dict is None:
            with open(CHECKOUT_MODEL_PATH, "r", encoding="utf-8") as f:
                model_dict = json.load(f)

        self.model_dict = model_dict
        self.tables: ModelTables = load_tables(model_dict)
        self.model_version: str = self.tables.model_version
        self.instance_store: InMemoryInstanceStore = instance_store or InMemoryInstanceStore()
        self.dedup: EventDeduplicator = deduplicator or EventDeduplicator()
        self.alert_store: SQLiteAlertStore = alert_store or SQLiteAlertStore(":memory:")
        self.alert_builder: AlertBuilder = alert_builder or AlertBuilder()

    def process_event(self, event_dict: dict[str, Any]) -> dict[str, Any] | None:
        """Process one API event through deduplication, instance tracking, and classification."""
        # 1. Deduplicate by event_id
        event_id = str(event_dict.get("event_id", ""))
        if event_id and self.dedup.is_duplicate(event_id):
            return None

        # 2. Lookup or create instance record
        res_type = event_dict.get("resource_type")
        res_id = event_dict.get("resource_id")
        if not res_type or not res_id:
            return None

        caller_sub = str(event_dict.get("subject", {}).get("sub", "anonymous"))
        caller_role = str(event_dict.get("subject", {}).get("role", "customer"))
        owner = caller_sub if caller_role == "customer" else "anonymous"

        instance = self.instance_store.get_or_create(
            resource_type=str(res_type),
            resource_id=str(res_id),
            initial_state=self.tables.initial_state,
            owner_subject=owner,
        )

        # 3. Classify event
        result = classify(instance, event_dict, self.tables)
        self.instance_store.update(instance)

        # 4. If violation, build and persist alert
        if result is not None and result.is_violation:
            alert = self.alert_builder.build_alert(
                instance=instance,
                event=event_dict,
                classification=result,
                model_version=self.model_version,
            )
            self.alert_store.save_alert(alert)
            return alert

        return None
