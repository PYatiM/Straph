"""Unit tests for ReplayRunner deterministic stream replay."""

from __future__ import annotations

import logging
from typing import Any

import pytest

from services.detector.worker import DetectorWorker
from services.evaluation.adapters import StraphDetectorAdapter
from services.evaluation.generators.attack_skip import generate_skip_payment_attack
from services.evaluation.replay_runner import ReplayExecutionMetrics, ReplayRunner


class MockStreamDetector:
    """Deterministic mock detector recording received event order and flagging violations."""

    def __init__(self, flag_event_ids: set[str] | None = None) -> None:
        self.flag_event_ids: set[str] = flag_event_ids or set()
        self.received_event_ids: list[str] = []
        self.call_count: int = 0

    def process_event_stream(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        self.call_count += 1
        self.received_event_ids = [str(e["event_id"]) for e in events]
        alerts: list[dict[str, Any]] = []
        for idx, ev in enumerate(events):
            ev_id = str(ev["event_id"])
            if ev_id in self.flag_event_ids:
                alerts.append(
                    {
                        "alert_id": f"00000000-0000-4000-8000-{idx:012d}",
                        "event_id": ev_id,
                        "label": "SKIPPED",
                        "rule": "R2",
                        "severity": "accepted",
                        "missing": ["POST /orders/{id}/payment", "POST /payments/webhook"],
                    }
                )
        return alerts


def test_replay_runner_event_ordering_and_alert_collection(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Confirm ReplayRunner preserves chronological event ordering, collects alerts, and logs metrics."""
    events, labels = generate_skip_payment_attack(order_id="1002", seed=42)
    violation_id = labels[2].event_id

    mock_detector = MockStreamDetector(flag_event_ids={violation_id})
    runner = ReplayRunner()

    with caplog.at_level(logging.INFO, logger="straph.evaluation.replay_runner"):
        alerts = runner.replay_stream(events, mock_detector)

    assert mock_detector.call_count == 1
    assert mock_detector.received_event_ids == [e["event_id"] for e in events]
    assert len(alerts) == 1
    assert alerts[0]["event_id"] == violation_id
    assert alerts[0]["label"] == "SKIPPED"
    assert alerts[0]["rule"] == "R2"

    # Runtime metrics check
    assert runner.last_metrics is not None
    assert isinstance(runner.last_metrics, ReplayExecutionMetrics)
    assert runner.last_metrics.detector_name == "MockStreamDetector"
    assert runner.last_metrics.events_processed == 3
    assert runner.last_metrics.alerts_emitted == 1
    assert runner.last_metrics.elapsed_ms > 0.0
    assert len(runner.execution_history) == 1
    assert any("Replay finished:" in rec.message for rec in caplog.records)


def test_replay_runner_100_percent_deterministic() -> None:
    """Replaying identical event stream twice produces identical alert semantic payloads."""
    events, _ = generate_skip_payment_attack(order_id="1002", seed=42)
    runner = ReplayRunner()

    mock_1 = MockStreamDetector(flag_event_ids={events[2]["event_id"]})
    mock_2 = MockStreamDetector(flag_event_ids={events[2]["event_id"]})

    alerts_run_1 = runner.replay_stream(events, mock_1)
    alerts_run_2 = runner.replay_stream(events, mock_2)

    assert alerts_run_1 == alerts_run_2

    # Also verify with real StraphDetectorAdapter across two fresh workers
    straph_run_1 = runner.replay_stream(events, StraphDetectorAdapter(DetectorWorker()))
    straph_run_2 = runner.replay_stream(events, StraphDetectorAdapter(DetectorWorker()))

    assert len(straph_run_1) == 1
    assert len(straph_run_2) == 1
    for key in ("event_id", "label", "rule", "severity", "missing", "expected_path", "observed"):
        assert straph_run_1[0][key] == straph_run_2[0][key]


def test_replay_runner_chronological_sort_option() -> None:
    """When sort_chronologically=True, out-of-order timestamps are sorted before processing."""
    events, _ = generate_skip_payment_attack(order_id="1002", seed=42)
    reversed_events = list(reversed(events))

    mock_detector = MockStreamDetector(flag_event_ids={events[2]["event_id"]})
    runner = ReplayRunner(sort_chronologically=True)
    alerts = runner.replay_stream(reversed_events, mock_detector)

    assert mock_detector.received_event_ids == [e["event_id"] for e in events]
    assert len(alerts) == 1


def test_replay_runner_validation_errors() -> None:
    """Invalid events or non-protocol detector raises TypeError."""
    runner = ReplayRunner()
    mock_detector = MockStreamDetector()

    with pytest.raises(TypeError, match="events must be a list"):
        runner.replay_stream("not-a-list", mock_detector)  # type: ignore[arg-type]

    with pytest.raises(TypeError, match="detector must implement StreamDetectorProtocol"):
        runner.replay_stream([], object())  # type: ignore[arg-type]
