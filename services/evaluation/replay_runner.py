"""Deterministic stream replay runner for evaluation detectors.

Feeds API event streams chronologically into any StreamDetectorProtocol-compliant
detector adapter, records runtime execution metrics, and returns produced alerts.
"""

from __future__ import annotations

import copy
import logging
import time
from dataclasses import dataclass
from typing import Any

from services.evaluation.adapters import StreamDetectorProtocol

log = logging.getLogger("straph.evaluation.replay_runner")


@dataclass(slots=True)
class ReplayExecutionMetrics:
    """Runtime execution telemetry captured during a single stream replay."""

    detector_name: str
    events_processed: int
    alerts_emitted: int
    elapsed_ms: float
    events_per_second: float


class ReplayRunner:
    """Deterministic runner feeding chronological event streams into detectors."""

    def __init__(self, *, sort_chronologically: bool = False) -> None:
        """Initialize the replay runner.

        Args:
            sort_chronologically: When True, sorts events by ts_completed/ts_received
                using stable sort before passing to the detector. Defaults to False
                (preserving exact caller stream order unless requested).
        """
        self.sort_chronologically: bool = sort_chronologically
        self.last_metrics: ReplayExecutionMetrics | None = None
        self.execution_history: list[ReplayExecutionMetrics] = []

    @staticmethod
    def _event_sort_key(item: tuple[int, dict[str, Any]]) -> tuple[str, int]:
        """Stable chronological sort key falling back to original index."""
        idx, event = item
        ts = str(event.get("ts_completed") or event.get("ts_received") or "")
        return (ts, idx)

    def replay_stream(
        self,
        events: list[dict[str, Any]],
        detector: StreamDetectorProtocol,
    ) -> list[dict[str, Any]]:
        """Feed events chronologically into detector.process_event_stream(events).

        Logs runtime execution metrics and returns all produced alert objects.

        Args:
            events: List of API event dictionaries.
            detector: Detector implementing StreamDetectorProtocol.

        Returns:
            List of alert dictionaries produced by the detector.

        Raises:
            TypeError: If events is not a list or detector does not satisfy
                StreamDetectorProtocol.
        """
        if not isinstance(events, list):
            raise TypeError(f"events must be a list of dicts, got {type(events).__name__}.")
        if not isinstance(detector, StreamDetectorProtocol):
            raise TypeError(
                f"detector must implement StreamDetectorProtocol, got {type(detector).__name__}."
            )

        # Deep-copy input stream to prevent in-place mutation across replays
        stream_copy: list[dict[str, Any]] = [copy.deepcopy(ev) for ev in events]

        if self.sort_chronologically and len(stream_copy) > 1:
            indexed = list(enumerate(stream_copy))
            indexed.sort(key=self._event_sort_key)
            stream_copy = [ev for _, ev in indexed]

        detector_name = detector.__class__.__name__
        start_ns = time.perf_counter_ns()
        raw_alerts = detector.process_event_stream(stream_copy)
        elapsed_ns = max(1, time.perf_counter_ns() - start_ns)

        alerts: list[dict[str, Any]] = [copy.deepcopy(a) for a in raw_alerts]
        elapsed_ms = elapsed_ns / 1_000_000.0
        eps = (len(stream_copy) / (elapsed_ns / 1_000_000_000.0)) if stream_copy else 0.0

        metrics = ReplayExecutionMetrics(
            detector_name=detector_name,
            events_processed=len(stream_copy),
            alerts_emitted=len(alerts),
            elapsed_ms=elapsed_ms,
            events_per_second=eps,
        )
        self.last_metrics = metrics
        self.execution_history.append(metrics)

        log.info(
            "Replay finished: detector=%s events=%d alerts=%d elapsed_ms=%.3f eps=%.1f",
            metrics.detector_name,
            metrics.events_processed,
            metrics.alerts_emitted,
            metrics.elapsed_ms,
            metrics.events_per_second,
        )
        return alerts
