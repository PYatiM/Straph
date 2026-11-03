"""Alert builder constructing schema-validated alert objects with OWASP BLA mapping."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import jsonschema

from services.detector.classifier import ClassificationResult, normalize_op_template
from services.detector.store import InstanceRecord

ROOT = Path(__file__).resolve().parents[2]
ALERT_SCHEMA_PATH = ROOT / "contracts" / "alert.schema.json"
OWASP_MAP_PATH = Path(__file__).parent / "owasp_map.json"


class AlertBuilder:
    """Constructs and validates alerts conforming to contracts/alert.schema.json."""

    def __init__(
        self,
        schema_path: Path | None = None,
        owasp_map_path: Path | None = None,
    ) -> None:
        s_path = schema_path or ALERT_SCHEMA_PATH
        with open(s_path, "r", encoding="utf-8") as f:
            self.schema: dict[str, Any] = json.load(f)

        m_path = owasp_map_path or OWASP_MAP_PATH
        with open(m_path, "r", encoding="utf-8") as f:
            self.owasp_map: dict[str, str] = json.load(f)

    def build_alert(
        self,
        instance: InstanceRecord,
        event: dict[str, Any],
        classification: ClassificationResult,
        model_version: str,
    ) -> dict[str, Any]:
        """Build schema-validated alert dictionary from instance, event, and classification."""
        alert_id = str(uuid.uuid4())
        event_id = str(event.get("event_id") or uuid.uuid4())

        ts = str(
            event.get("ts_completed")
            or event.get("ts_received")
            or datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        )
        # Ensure timestamp ends with Z or timezone offset for ISO format
        if not ts.endswith("Z") and "+" not in ts and "-" not in ts[10:]:
            ts = f"{ts}Z"

        op_obj = event.get("operation", {})
        method = str(op_obj.get("method", "POST"))
        path_template = str(op_obj.get("path_template", ""))
        observed_op = normalize_op_template(method, path_template)

        rule = str(classification.rule or "R4")
        label = str(classification.label or "UNMODELED")
        owasp_class = self.owasp_map.get(rule, "BLA6")

        alert: dict[str, Any] = {
            "alert_id": alert_id,
            "event_id": event_id,
            "timestamp": ts,
            "label": label,
            "rule": rule,
            "severity": classification.severity,
            "subject": dict(event.get("subject", {})),
            "resource": {
                "type": str(instance.resource_type),
                "id": str(instance.resource_id),
            },
            "expected_path": list(classification.expected_path),
            "observed": {
                "operation": observed_op,
                "state": instance.current_state,
                "status_code": int(event.get("status_code", 200)),
            },
            "missing": list(classification.missing),
            "owasp_class": owasp_class,
            "flags": {
                "tainted": bool(instance.tainted),
                "late": False,
                "retry": bool(event.get("idempotency_key") is not None),
            },
            "model_version": str(model_version or "unknown"),
        }

        if classification.secondary_label is not None:
            alert["secondary_label"] = classification.secondary_label

        # Validate strictly against contracts/alert.schema.json
        jsonschema.validate(instance=alert, schema=self.schema)

        return alert
