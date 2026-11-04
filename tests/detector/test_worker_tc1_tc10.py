"""End-to-end integration tests for DetectorWorker across test cases TC1 to TC10."""

from __future__ import annotations

from typing import Any

import pytest

from services.detector.worker import DetectorWorker
from tests.detector.fixtures import ALL_FIXTURES


@pytest.fixture
def worker() -> DetectorWorker:
    """Create fresh DetectorWorker instance."""
    return DetectorWorker()


@pytest.mark.parametrize("tc_name", list(ALL_FIXTURES.keys()))
def test_worker_tc1_to_tc10_end_to_end(tc_name: str) -> None:
    """Feed each fixture TC1..TC10 through DetectorWorker.process_event().

    Assert:
    - TC1, TC8, TC9, TC10 produce NO alerts.
    - TC2, TC3, TC4, TC5, TC6, TC7 produce exact expected alert labels and rules.
    """
    worker = DetectorWorker()
    get_events, get_expected = ALL_FIXTURES[tc_name]

    events = get_events()
    expected_alerts = get_expected()

    actual_alerts: list[dict[str, Any]] = []
    for event in events:
        alert = worker.process_event(event)
        if alert is not None:
            actual_alerts.append(alert)

    if not expected_alerts:
        assert actual_alerts == [], f"{tc_name} produced unexpected alerts: {actual_alerts}"
    else:
        assert len(actual_alerts) == len(expected_alerts), (
            f"{tc_name} expected {len(expected_alerts)} alerts, got {len(actual_alerts)}"
        )
        for actual, expected in zip(actual_alerts, expected_alerts, strict=True):
            assert actual["label"] == expected["label"], (
                f"{tc_name} label mismatch: {actual['label']} != {expected['label']}"
            )
            assert actual["rule"] == expected["rule"], (
                f"{tc_name} rule mismatch: {actual['rule']} != {expected['rule']}"
            )
            assert actual["severity"] == expected["severity"], (
                f"{tc_name} severity mismatch: {actual['severity']} != {expected['severity']}"
            )
            if "secondary_label" in expected and expected["secondary_label"] is not None:
                assert actual.get("secondary_label") == expected["secondary_label"], (
                    f"{tc_name} secondary_label mismatch: {actual.get('secondary_label')} != {expected['secondary_label']}"
                )


def test_benign_test_cases_produce_zero_alerts(worker: DetectorWorker) -> None:
    """TC1, TC8, TC9, TC10 must produce zero alerts."""
    for tc in ["tc1", "tc8", "tc9", "tc10"]:
        w = DetectorWorker()
        events, _ = ALL_FIXTURES[tc]
        alerts = [w.process_event(e) for e in events() if w.process_event(e) is not None]
        assert alerts == [], f"{tc} produced alerts: {alerts}"


def test_violation_test_cases_match_exact_rules(worker: DetectorWorker) -> None:
    """Verify specific violation mappings for TC2..TC7."""
    expected_mapping = {
        "tc2": ("SKIPPED", "R2"),
        "tc3": ("OUT_OF_ORDER", "R3"),
        "tc4": ("REPLAYED", "R1"),
        "tc5": ("IDENTITY_MISMATCH", "R0"),
        "tc6": ("IDENTITY_MISMATCH", "R0"),
        "tc7": ("SKIPPED", "R2"),
    }

    for tc, (expected_label, expected_rule) in expected_mapping.items():
        w = DetectorWorker()
        events, _ = ALL_FIXTURES[tc]
        alerts = [a for e in events() if (a := w.process_event(e)) is not None]
        assert len(alerts) == 1, f"{tc} expected 1 alert, got {len(alerts)}"
        assert alerts[0]["label"] == expected_label
        assert alerts[0]["rule"] == expected_rule
