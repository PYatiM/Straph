"""Deterministic IDS classifier evaluating events against workflow model tables."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from services.detector.store import InstanceRecord
from services.detector.tables import ModelTables

UUID_OR_ID_SEGMENT = re.compile(r"^[0-9a-fA-F\-]{4,}$|^[0-9]+$")


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

    return None
