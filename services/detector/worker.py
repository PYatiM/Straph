"""Detector worker evaluating events against reference workflow models."""

from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from services.dashboard.alert_store import SQLiteAlertStore
from services.detector.constants import RuleId, SeverityLevel, ViolationLabel
from services.model_builder.cache import InMemoryModelGraph

log = logging.getLogger("straph.detector.worker")

ROOT_DIR = Path(__file__).resolve().parents[2]
CHECKOUT_MODEL_PATH = ROOT_DIR / "contracts" / "examples" / "model_checkout.json"
UUID_OR_ID_SEGMENT = re.compile(r"^[0-9a-fA-F\-]{4,}$|^[0-9]+$")


def normalize_op_template(method: str, path_template: str) -> str:
    """Normalize path template to match reference model convention (e.g. /orders/{id}/ship)."""
    clean = path_template.split("?", 1)[0].split("#", 1)[0]
    segments = [s for s in clean.split("/") if s]
    if (
        segments
        and segments[0] == "orders"
        and len(segments) >= 2
        and (UUID_OR_ID_SEGMENT.match(segments[1]) or segments[1] != "items")
    ):
        segments[1] = "{id}"
        return f"{method.upper()} /" + "/".join(segments)
    return f"{method.upper()} " + (
        path_template if path_template.startswith("/") else f"/{path_template}"
    )


@dataclass
class InstanceRecord:
    """Tracks state and history for one business object instance."""

    key: str
    resource_type: str
    resource_id: str
    state: str | None = None
    owner_sub: str | None = None
    history: list[dict[str, Any]] = field(default_factory=list)
    tainted: bool = False
    event_count: int = 0


class DetectorWorker:
    """Deterministic IDS engine evaluating API events against reference workflow models."""

    def __init__(
        self,
        model_dict: dict[str, Any] | None = None,
        alert_store: SQLiteAlertStore | None = None,
    ) -> None:
        if model_dict is None:
            with open(CHECKOUT_MODEL_PATH, "r", encoding="utf-8") as f:
                model_dict = json.load(f)

        self.model_dict = model_dict
        self.model_graph = InMemoryModelGraph(model_dict)
        self.alert_store = alert_store
        self.instances: dict[str, InstanceRecord] = {}

    def _get_or_create_instance(self, event: dict[str, Any]) -> InstanceRecord | None:
        res_type = event.get("resource_type")
        res_id = event.get("resource_id")
        if not res_type or not res_id:
            return None
        key = f"{res_type}:{res_id}"
        if key not in self.instances:
            self.instances[key] = InstanceRecord(
                key=key,
                resource_type=str(res_type),
                resource_id=str(res_id),
                state=self.model_graph.initial_state,
            )
        return self.instances[key]

    def process_event(self, event: dict[str, Any]) -> dict[str, Any] | None:
        """Evaluate an API event through the 6-tier classification hierarchy."""
        instance = self._get_or_create_instance(event)
        if instance is None:
            return None

        method = event.get("operation", {}).get("method", "").upper()
        raw_path = event.get("operation", {}).get("path_template", "")
        op_template = normalize_op_template(method, raw_path)

        current_state = instance.state or self.model_graph.initial_state
        status_code = int(event.get("status_code", 200))
        is_success = 200 <= status_code < 300
        caller_sub = event.get("subject", {}).get("sub", "")
        caller_role = event.get("subject", {}).get("role", "")

        # Track owner on initial creation
        if instance.owner_sub is None and caller_role == "customer" and caller_sub:
            instance.owner_sub = caller_sub

        alert_label: ViolationLabel | None = None
        alert_rule: RuleId | None = None
        missing_ops: list[str] = []
        owasp_class: str = "BLA6"

        # 1. R4 (UNMODELED): Operation not found in reference model
        all_model_ops = {
            f"{t['operation']['method']} {t['operation']['path_template']}"
            for t in self.model_dict.get("transitions", [])
        }
        if op_template not in all_model_ops:
            alert_label = ViolationLabel.UNMODELED
            alert_rule = RuleId.R4
            owasp_class = "API9:2023 Improper Inventory Management"

        # 2. R0 (IDENTITY_MISMATCH): Caller is not instance owner and lacks cross-object role permission
        elif (
            instance.owner_sub is not None
            and caller_sub != instance.owner_sub
            and caller_role not in self.model_graph.get_allowed_roles(current_state, op_template)
            and caller_role not in ("warehouse", "carrier", "payment-gateway", "support", "admin")
        ):
            alert_label = ViolationLabel.IDENTITY_MISMATCH
            alert_rule = RuleId.R0
            owasp_class = "API1:2023 Broken Object Level Authorization"

        # 3. Check Legal Advance
        else:
            legal_next = self.model_graph.get_legal_next_states(current_state, op_template)
            if legal_next:
                # Valid transition from current state
                target_state = legal_next[0]
                if is_success:
                    instance.state = target_state
                    instance.history.append(event)
                instance.event_count += 1
                return None  # Benign advance, no alert

            # 4. R1 (REPLAYED): Illegal at current_state, marked one_shot=True, and exists in accepted history
            is_single_use = self.model_graph.is_single_use(op_template)
            already_in_history = any(
                normalize_op_template(
                    h.get("operation", {}).get("method", ""),
                    h.get("operation", {}).get("path_template", ""),
                )
                == op_template
                for h in instance.history
            )
            if is_single_use and already_in_history:
                alert_label = ViolationLabel.REPLAYED
                alert_rule = RuleId.R1
                owasp_class = "API6:2023 Unrestricted Access to Sensitive Business Flows"

            else:
                # 5. R2 (SKIPPED): Illegal at current_state, but valid source state is reachable ahead
                source_states = self.model_graph.get_source_states(op_template)
                reachable_sources = [
                    s
                    for s in source_states
                    if self.model_graph.is_state_reachable(current_state, s)
                ]

                if reachable_sources:
                    # Pick the shortest missing path among reachable sources
                    best_missing: list[str] = []
                    for src in reachable_sources:
                        p = self.model_graph.get_shortest_path(current_state, src)
                        if not best_missing or len(p) < len(best_missing):
                            best_missing = p

                    alert_label = ViolationLabel.SKIPPED
                    alert_rule = RuleId.R2
                    missing_ops = best_missing
                    owasp_class = "BLA6"

                # 6. R3 (OUT_OF_ORDER): Illegal at current_state and no valid source state reachable ahead
                else:
                    alert_label = ViolationLabel.OUT_OF_ORDER
                    alert_rule = RuleId.R3
                    owasp_class = "API6:2023 Unrestricted Access to Sensitive Business Flows"

        # Taint Rule: If illegal operation returned 2xx, mark tainted and resynchronize state
        severity = SeverityLevel.ATTEMPTED
        if is_success:
            instance.tainted = True
            severity = SeverityLevel.ACCEPTED
            # Resynchronize state to target state of the operation
            for t in self.model_dict.get("transitions", []):
                t_op = f"{t['operation']['method']} {t['operation']['path_template']}"
                if t_op == op_template:
                    instance.state = t.get("to_state")
                    break
            instance.history.append(event)

        instance.event_count += 1

        # Construct alert conforming to contracts/alert.schema.json
        expected_path = [
            "POST /orders",
            "PUT /orders/{id}/address",
            "POST /orders/{id}/payment",
            "POST /payments/webhook",
            "POST /orders/{id}/fulfill",
        ]
        alert = {
            "alert_id": str(uuid.uuid4()),
            "event_id": event.get("event_id", str(uuid.uuid4())),
            "timestamp": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "label": alert_label.value if alert_label else "UNKNOWN",
            "rule": alert_rule.value if alert_rule else "UNKNOWN",
            "severity": severity.value,
            "subject": {
                "identity": caller_sub or "anonymous",
                "role": caller_role or "unknown",
            },
            "resource": {
                "type": instance.resource_type,
                "id": instance.resource_id,
            },
            "expected_path": expected_path,
            "observed": {
                "operation": op_template,
                "state": current_state,
                "status_code": status_code,
            },
            "missing": missing_ops,
            "owasp_class": owasp_class,
            "flags": {
                "tainted": instance.tainted,
                "late": False,
                "retry": False,
            },
            "model_version": str(self.model_dict.get("model_version", "unknown")),
            "rank_score": None,
        }

        if self.alert_store is not None:
            self.alert_store.save_alert(alert)

        return alert
