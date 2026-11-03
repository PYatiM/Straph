"""Deterministic IDS classifier evaluating events against workflow model tables."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from services.detector.store import InstanceRecord
from services.detector.tables import ModelTables

UUID_OR_ID_SEGMENT = re.compile(r"^[0-9a-fA-F\-]{4,}$|^[0-9]+$")
CROSS_OBJECT_ROLES = {"support", "warehouse", "carrier", "payment-gateway", "admin"}


def normalize_path(path: str) -> str:
    """Normalize path parameter segments (e.g. /orders/1001/address -> /orders/{id}/address)."""
    clean = path.split("?", 1)[0].split("#", 1)[0]
    segments = [s for s in clean.split("/") if s]
    if (
        segments
        and segments[0] == "orders"
        and len(segments) >= 2
        and (UUID_OR_ID_SEGMENT.match(segments[1]) or segments[1] != "items")
    ):
        segments[1] = "{id}"
        return "/" + "/".join(segments)
    return path if path.startswith("/") else f"/{path}"


def normalize_op_template(method: str, path_template: str) -> str:
    """Normalize method and path template to canonical form (e.g. POST /orders/{id}/fulfill)."""
    norm_path = normalize_path(path_template)
    m = method.strip().upper()
    return f"{m} {norm_path}" if m else norm_path


def history_has_op(history: list[dict[str, Any]], op_template: str, norm_path: str) -> bool:
    """Check if instance history contains accepted occurrence of operation."""
    for h in history:
        h_op = h.get("operation")
        if isinstance(h_op, str):
            if h_op in (op_template, norm_path) or normalize_path(h_op) == norm_path:
                return True
        elif isinstance(h_op, dict):
            h_path = normalize_path(str(h_op.get("path_template", "")))
            if h_path == norm_path:
                return True
    return False


@dataclass
class ClassificationResult:
    """Result of classifying an API event against reference model."""

    is_violation: bool
    label: str | None = None
    rule: str | None = None
    secondary_label: str | None = None
    severity: str = "attempted"
    missing: list[str] = field(default_factory=list)
    expected_path: list[str] = field(default_factory=list)


def _compute_secondary_step(
    instance: InstanceRecord,
    tables: ModelTables,
    op_template: str,
    norm_path: str,
) -> tuple[str, list[str], list[str]]:
    """Compute secondary step label, missing ops, and expected path for R0 violations."""
    valid_sources = tables.source_states.get(op_template, set())

    # Legal advance
    if instance.current_state in valid_sources:
        target_key = (instance.current_state, op_template)
        if target_key not in tables.target_states:
            target_key = (instance.current_state, norm_path)
        target = tables.target_states.get(target_key, instance.current_state)
        label = "FULFILLED" if target == "FULFILLMENT_QUEUED" else target
        return (label, [], [op_template])

    # Replayed
    is_one_shot = (op_template in tables.one_shot_ops) or (norm_path in tables.one_shot_ops)
    if is_one_shot and history_has_op(instance.history, op_template, norm_path):
        return ("REPLAYED", [], [])

    # Skipped
    reachable_ahead = tables.reachability.get(instance.current_state, set())
    reachable_sources = [
        s for s in valid_sources if s in reachable_ahead and s != instance.current_state
    ]
    if reachable_sources:
        best_missing: list[str] | None = None
        for s in reachable_sources:
            path = tables.shortest_paths.get((instance.current_state, s))
            if path is not None and (best_missing is None or len(path) < len(best_missing)):
                best_missing = path
        missing_ops = best_missing if best_missing is not None else []
        return ("SKIPPED", missing_ops, missing_ops + [op_template])

    # Out of order
    return ("OUT_OF_ORDER", [], [])


def _handle_violation_taint(
    instance: InstanceRecord,
    tables: ModelTables,
    op_template: str,
    norm_path: str,
    status_code: int,
    ts: str,
    result: ClassificationResult,
) -> ClassificationResult:
    """Taint instance and resynchronize state on accepted violation."""
    if 200 <= status_code < 300:
        instance.tainted = True
        # Find target state of the accepted operation across known transitions
        for (_from_st, op_key), to_st in tables.target_states.items():
            if op_key in (op_template, norm_path):
                instance.current_state = to_st
                break
        instance.history.append({"operation": op_template, "status": status_code, "ts": ts, "tainted": True})
    else:
        instance.history.append({"operation": op_template, "status": status_code, "ts": ts, "tainted": False})
    return result


def classify(
    instance: InstanceRecord,
    event: dict[str, Any],
    tables: ModelTables,
) -> ClassificationResult | None:
    """Classify an event against reference model tables. Returns None if benign."""
    op_obj = event.get("operation", {})
    raw_path = str(op_obj.get("path_template", ""))
    method = str(op_obj.get("method", "POST")).upper()

    norm_path = normalize_path(raw_path)
    full_op = normalize_op_template(method, raw_path)

    # 1. Rule R4: UNMODELED
    # Check if operation path or full op template exists in reference model source states
    is_modeled = (
        norm_path in tables.source_states
        or full_op in tables.source_states
        or raw_path in tables.source_states
    )
    if not is_modeled:
        return ClassificationResult(
            is_violation=True,
            label="UNMODELED",
            rule="R4",
            secondary_label=None,
            severity="attempted",
            missing=[],
            expected_path=[],
        )

    # Canonical operation key preferred for transitions and history
    op_template = full_op if full_op in tables.source_states else norm_path
    status_code = int(event.get("status_code", 200))
    ts = str(event.get("ts_completed", event.get("ts_received", "")))
    severity = "accepted" if 200 <= status_code < 300 else "attempted"

    # Idempotency handling
    raw_idem_key = event.get("idempotency_key")
    is_retry = False
    if raw_idem_key:
        idem_key = str(raw_idem_key)
        key_with_op = f"{idem_key}:{op_template}"
        if key_with_op in instance.idempotency_keys or idem_key in instance.idempotency_keys:
            is_retry = True
        else:
            instance.idempotency_keys.add(key_with_op)
            instance.idempotency_keys.add(idem_key)

    # Synchronize initial state if instance starts at generic "START"
    if instance.current_state == "START" and tables.initial_state != "START":
        instance.current_state = tables.initial_state

    # Extract subject info
    caller_sub = str(event.get("subject", {}).get("sub", ""))
    caller_role = str(event.get("subject", {}).get("role", ""))

    # Bind owner on initial customer interaction if unbound
    if instance.owner_subject in ("anonymous", "", None) and caller_role == "customer" and caller_sub:
        instance.owner_subject = caller_sub

    # 2. Rule R0: IDENTITY_MISMATCH (Evaluated BEFORE legality checks)
    # Check 1: Caller is not instance owner and lacks cross-object role permission
    if (
        instance.owner_subject not in ("anonymous", "", None)
        and caller_sub != instance.owner_subject
        and caller_role not in CROSS_OBJECT_ROLES
    ):
        step_label, missing_ops, exp_path = _compute_secondary_step(
            instance, tables, op_template, norm_path
        )
        res = ClassificationResult(
            is_violation=True,
            label="IDENTITY_MISMATCH",
            rule="R0",
            secondary_label=step_label,
            severity=severity,
            missing=missing_ops,
            expected_path=exp_path,
        )
        return _handle_violation_taint(instance, tables, op_template, norm_path, status_code, ts, res)

    # Check 2: Caller role is not authorized for the requested operation
    allowed_roles = tables.allowed_roles.get(op_template) or tables.allowed_roles.get(norm_path)
    if allowed_roles and caller_role not in allowed_roles:
        step_label, missing_ops, exp_path = _compute_secondary_step(
            instance, tables, op_template, norm_path
        )
        res = ClassificationResult(
            is_violation=True,
            label="IDENTITY_MISMATCH",
            rule="R0",
            secondary_label=step_label,
            severity=severity,
            missing=missing_ops,
            expected_path=exp_path,
        )
        return _handle_violation_taint(instance, tables, op_template, norm_path, status_code, ts, res)

    # 3. Check Legal Advance
    valid_sources = tables.source_states.get(op_template, set())
    if instance.current_state in valid_sources:
        if 200 <= status_code < 300:
            target_key = (instance.current_state, op_template)
            if target_key not in tables.target_states:
                target_key = (instance.current_state, norm_path)
            new_state = tables.target_states[target_key]
            instance.current_state = new_state
            instance.history.append({"operation": op_template, "status": status_code, "ts": ts})
            return None

        # Non-2xx failed call preserves current state
        instance.history.append({"operation": op_template, "status": status_code, "ts": ts})
        return None

    # If this was an idempotent retry of an already completed or advanced operation, return None
    if is_retry:
        return None

    # 4. Rule R1: REPLAYED (one-shot operation executed again)
    is_one_shot = (op_template in tables.one_shot_ops) or (norm_path in tables.one_shot_ops)
    if is_one_shot and history_has_op(instance.history, op_template, norm_path):
        res = ClassificationResult(
            is_violation=True,
            label="REPLAYED",
            rule="R1",
            secondary_label=None,
            severity=severity,
            missing=[],
            expected_path=[],
        )
        return _handle_violation_taint(instance, tables, op_template, norm_path, status_code, ts, res)

    # 5. Rule R2: SKIPPED (reachable source state ahead)
    reachable_ahead = tables.reachability.get(instance.current_state, set())
    reachable_sources = [
        s for s in valid_sources if s in reachable_ahead and s != instance.current_state
    ]
    if reachable_sources:
        best_missing: list[str] | None = None
        for s in reachable_sources:
            path = tables.shortest_paths.get((instance.current_state, s))
            if path is not None and (best_missing is None or len(path) < len(best_missing)):
                best_missing = path

        missing_ops = best_missing if best_missing is not None else []
        res = ClassificationResult(
            is_violation=True,
            label="SKIPPED",
            rule="R2",
            secondary_label=None,
            severity=severity,
            missing=missing_ops,
            expected_path=missing_ops + [op_template],
        )
        return _handle_violation_taint(instance, tables, op_template, norm_path, status_code, ts, res)

    # 6. Rule R3: OUT_OF_ORDER (no source state reachable ahead)
    res = ClassificationResult(
        is_violation=True,
        label="OUT_OF_ORDER",
        rule="R3",
        secondary_label=None,
        severity=severity,
        missing=[],
        expected_path=[],
    )
    return _handle_violation_taint(instance, tables, op_template, norm_path, status_code, ts, res)
