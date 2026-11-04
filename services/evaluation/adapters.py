"""Common stream detector protocol and baseline adapters for evaluation.

Provides a unified StreamDetectorProtocol allowing Straph and all sequence baselines
(NGram, FirstOrderMarkov, TraceInvariant) to process event streams interchangeably.
"""

from __future__ import annotations

import math
import uuid
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable

from services.detector.classifier import normalize_op_template, normalize_path
from services.detector.worker import DetectorWorker
from services.evaluation.baselines.markov import FirstOrderMarkovBaseline
from services.evaluation.baselines.ngram import NGramBaseline
from services.evaluation.baselines.trace_invariant import TraceInvariantBaseline

__all__ = [
    "MarkovDetectorAdapter",
    "NGramDetectorAdapter",
    "StraphDetectorAdapter",
    "StreamDetectorProtocol",
    "TraceInvariantDetectorAdapter",
]


@runtime_checkable
class StreamDetectorProtocol(Protocol):
    """Protocol for event stream detectors returning standardized alert dictionaries."""

    def process_event_stream(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Process a stream of API events and return any detected alert dicts."""
        ...


def _extract_op(event: dict[str, Any]) -> str:
    """Extract and normalize operation string from an event dictionary."""
    op = event.get("operation")
    if isinstance(op, dict):
        method = str(op.get("method", "POST")).upper()
        path = str(op.get("path_template", ""))
        clean_path = normalize_path(path)
        return normalize_op_template(method, clean_path)
    if isinstance(op, str):
        parts = op.split(" ", 1)
        if len(parts) == 2:
            return normalize_op_template(parts[0], normalize_path(parts[1]))
        return normalize_path(op)
    return ""


def _instance_key(event: dict[str, Any]) -> str:
    """Derive instance tracking key from event resource or session identifiers."""
    res_type = event.get("resource_type")
    res_id = event.get("resource_id")
    if res_type and res_id:
        return f"{res_type}:{res_id}"
    session_id = event.get("session_id")
    if session_id:
        return f"session:{session_id}"
    return "default"


def _make_alert(
    event: dict[str, Any],
    label: str,
    rule: str,
    model_version: str = "baseline-1.0",
    missing: list[str] | None = None,
    expected_path: list[str] | None = None,
    rank_score: float | None = None,
) -> dict[str, Any]:
    """Construct schema-validated standardized alert dictionary."""
    alert_id = str(uuid.uuid4())
    raw_event_id = str(event.get("event_id") or "")
    # Use existing event_id if valid UUID length else generate one
    event_id = raw_event_id if len(raw_event_id) == 36 else str(uuid.uuid4())

    ts = str(
        event.get("ts_completed")
        or event.get("ts_received")
        or datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    )
    if not ts.endswith("Z") and "+" not in ts and "-" not in ts[10:]:
        ts = f"{ts}Z"

    op = event.get("operation", {})
    if isinstance(op, dict):
        method = str(op.get("method", "POST"))
        path_template = str(op.get("path_template", ""))
        observed_op = normalize_op_template(method, path_template)
    elif isinstance(op, str):
        observed_op = op
    else:
        observed_op = "UNKNOWN"

    status_code = int(event.get("status_code", 200))
    severity = "accepted" if 200 <= status_code < 300 else "attempted"

    alert: dict[str, Any] = {
        "alert_id": alert_id,
        "event_id": event_id,
        "timestamp": ts,
        "label": label,
        "rule": rule,
        "severity": severity,
        "subject": dict(event.get("subject", {})),
        "resource": {
            "type": str(event.get("resource_type", "Order")),
            "id": str(event.get("resource_id", "1001")),
        },
        "expected_path": list(expected_path or []),
        "observed": {
            "operation": observed_op,
            "state": None,
            "status_code": status_code,
        },
        "missing": list(missing or []),
        "owasp_class": "BLA6",
        "flags": {
            "tainted": False,
            "late": False,
            "retry": bool(event.get("idempotency_key") is not None),
        },
        "model_version": str(model_version),
    }
    if rank_score is not None:
        alert["rank_score"] = float(min(1.0, max(0.0, rank_score)))
    return alert


class StraphDetectorAdapter:
    """Adapts Straph DetectorWorker to StreamDetectorProtocol."""

    def __init__(self, worker: DetectorWorker) -> None:
        self.worker: DetectorWorker = worker

    def process_event_stream(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Process event stream sequentially and collect raised alerts."""
        alerts: list[dict[str, Any]] = []
        for event in events:
            alert = self.worker.process_event(event)
            if alert is not None:
                alerts.append(alert)
        return alerts


class NGramDetectorAdapter:
    """Adapts NGramBaseline frequency model to StreamDetectorProtocol."""

    def __init__(self, baseline: NGramBaseline, threshold: float) -> None:
        self.baseline: NGramBaseline = baseline
        self.threshold: float = float(threshold)

    def process_event_stream(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Evaluate events sequentially; flag alerts when n-gram score exceeds threshold."""
        alerts: list[dict[str, Any]] = []
        histories: dict[str, list[str]] = {}

        for event in events:
            key = _instance_key(event)
            seq = histories.setdefault(key, [])
            op = _extract_op(event)
            seq.append(op)

            # Evaluate only once an n-gram window of size n is available
            if len(seq) < self.baseline.n:
                continue

            window = seq[-self.baseline.n :]
            score = self.baseline.score_sequence(window)
            if score >= self.threshold:
                alert = _make_alert(
                    event=event,
                    label="SKIPPED",
                    rule="R2",
                    model_version="ngram-baseline",
                    expected_path=[],
                    missing=[],
                    rank_score=min(1.0, score / max(self.threshold * 2.0, 1.0)),
                )
                alerts.append(alert)

        return alerts


class MarkovDetectorAdapter:
    """Adapts FirstOrderMarkovBaseline model to StreamDetectorProtocol."""

    def __init__(self, baseline: FirstOrderMarkovBaseline, threshold: float) -> None:
        self.baseline: FirstOrderMarkovBaseline = baseline
        self.threshold: float = float(threshold)

    def process_event_stream(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Evaluate events sequentially; flag alerts when transition score exceeds threshold."""
        alerts: list[dict[str, Any]] = []
        histories: dict[str, list[str]] = {}

        for event in events:
            key = _instance_key(event)
            seq = histories.setdefault(key, [])
            op = _extract_op(event)
            seq.append(op)

            if len(seq) >= 2:
                from_op = seq[-2]
                to_op = seq[-1]
                prob = self.baseline.get_transition_prob(from_op, to_op)
                vocab_size = max(len(self.baseline._vocab), 1)
                eps = self.baseline.epsilon
                smoothed_p = (1.0 - eps) * prob + eps * (1.0 / vocab_size)
                score = -math.log(smoothed_p)

                if score >= self.threshold:
                    alert = _make_alert(
                        event=event,
                        label="OUT_OF_ORDER",
                        rule="R3",
                        model_version="markov-baseline",
                        expected_path=[],
                        missing=[],
                        rank_score=min(1.0, score / max(self.threshold * 2.0, 1.0)),
                    )
                    alerts.append(alert)

        return alerts


class TraceInvariantDetectorAdapter:
    """Adapts TraceInvariantBaseline model to StreamDetectorProtocol."""

    def __init__(self, baseline: TraceInvariantBaseline) -> None:
        self.baseline: TraceInvariantBaseline = baseline

    def process_event_stream(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Evaluate events sequentially against trace invariant pairs and single-use rules."""
        alerts: list[dict[str, Any]] = []
        histories: dict[str, list[str]] = {}

        for event in events:
            key = _instance_key(event)
            seq = histories.setdefault(key, [])
            op = _extract_op(event)
            seq.append(op)

            # Check 1: single-use operation repeated
            if op in self.baseline.single_use_ops and seq.count(op) > 1:
                alert = _make_alert(
                    event=event,
                    label="REPLAYED",
                    rule="R1",
                    model_version="trace-invariant-baseline",
                )
                alerts.append(alert)
                continue

            # Check 2: adjacent transition not in allowed pairs
            if len(seq) >= 2:
                from_op = seq[-2]
                to_op = seq[-1]
                if (from_op, to_op) not in self.baseline.allowed_pairs:
                    alert = _make_alert(
                        event=event,
                        label="OUT_OF_ORDER",
                        rule="R3",
                        model_version="trace-invariant-baseline",
                    )
                    alerts.append(alert)

        return alerts
