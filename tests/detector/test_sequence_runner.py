"""Tests for the offline sequence runner using a dummy classifier."""

from typing import Any

from services.detector.runner import (
    AlertRaised,
    InstanceState,
    instance_key,
    run_sequence,
)
from tests.detector.fixtures import get_tc1_events, get_tc2_events, get_tc4_events

MODEL: dict[str, Any] = {"model_version": "dummy-v0"}


def _dummy_classify(
    instance: InstanceState, event: dict[str, Any], model: dict[str, Any]
) -> dict[str, Any] | None:
    """Return None for benign calls; raise AlertRaised if fulfill lacks a prior payment."""
    if event["path_template"].endswith("/fulfill"):
        paid: bool = any(e["path_template"].endswith("/payment") for e in instance.history)
        if not paid:
            raise AlertRaised(
                {
                    "label": "SKIPPED",
                    "event_id": event["event_id"],
                    "instance": instance.key,
                    "model_version": model["model_version"],
                }
            )
    return None


def test_tc1_processes_without_error_and_raises_nothing() -> None:
    """Assert the legal TC1 path runs cleanly and yields no alerts."""
    assert run_sequence(MODEL, get_tc1_events(), _dummy_classify) == []


def test_tc2_known_violation_raises_single_alert() -> None:
    """Assert the skipped-payment fulfill in TC2 yields exactly one collected alert."""
    events: list[dict[str, Any]] = get_tc2_events()
    alerts: list[dict[str, Any]] = run_sequence(MODEL, events, _dummy_classify)
    assert len(alerts) == 1
    assert alerts[0]["label"] == "SKIPPED"
    assert alerts[0]["event_id"] == events[-1]["event_id"]
    assert alerts[0]["instance"] == "Order:1002"
    assert alerts[0]["model_version"] == "dummy-v0"


def test_returned_alert_dicts_are_collected_too() -> None:
    """Assert a classifier that returns alert dicts (instead of raising) is supported."""

    def returning(
        instance: InstanceState, event: dict[str, Any], model: dict[str, Any]
    ) -> dict[str, Any] | None:
        return {"event_id": event["event_id"]} if event["status"] == 201 else None

    alerts: list[dict[str, Any]] = run_sequence(MODEL, get_tc1_events(), returning)
    assert len(alerts) == 1


def test_state_is_maintained_sequentially_per_instance() -> None:
    """Assert the classifier sees each instance's record as of just before the event."""
    seen: list[tuple[str, int, str | None, int]] = []

    def spy(
        instance: InstanceState, event: dict[str, Any], model: dict[str, Any]
    ) -> dict[str, Any] | None:
        seen.append(
            (instance.key, instance.event_count, instance.last_event_id, len(instance.history))
        )
        return None

    events: list[dict[str, Any]] = get_tc1_events()
    run_sequence(MODEL, events, spy)

    assert [s[0] for s in seen] == [
        "Cart:cart-1001",
        "Order:1001",
        "Order:1001",
        "Order:1001",
        "Order:1001",
        "Order:1001",
        "Order:1001",
    ]
    # Order:1001 records grow 0,1,2,... across its six events; the cart starts fresh.
    assert [s[1] for s in seen] == [0, 0, 1, 2, 3, 4, 5]
    assert [s[3] for s in seen] == [0, 0, 1, 2, 3, 4, 5]
    assert seen[0][2] is None
    assert seen[2][2] == events[1]["event_id"]
    assert seen[-1][2] == events[-2]["event_id"]


def test_classifier_can_mutate_instance_state_across_events() -> None:
    """Assert state mutated by the classifier persists to later events of the same instance."""
    states: list[str | None] = []

    def advancing(
        instance: InstanceState, event: dict[str, Any], model: dict[str, Any]
    ) -> dict[str, Any] | None:
        states.append(instance.state)
        instance.state = f"after:{event['path_template']}"
        return None

    run_sequence(MODEL, get_tc2_events(), advancing)
    assert states == [None, "after:/orders", "after:/orders/1002/address"]


def test_only_2xx_events_enter_history() -> None:
    """Assert non-2xx events are counted but not recorded in accepted history."""
    events: list[dict[str, Any]] = [dict(e) for e in get_tc4_events()[:2]]
    events[1]["status"] = 500
    events[1]["resource_type"] = events[0]["resource_type"]
    events[1]["resource_id"] = events[0]["resource_id"]
    captured: list[InstanceState] = []

    def capture(
        instance: InstanceState, event: dict[str, Any], model: dict[str, Any]
    ) -> dict[str, Any] | None:
        captured.append(instance)
        return None

    run_sequence(MODEL, events, capture)
    # events[0] is a Cart event; both share its instance after the rewrite above.
    assert captured[0] is captured[1]
    assert captured[0].event_count == 2
    assert captured[0].history == [events[0]]


def test_empty_sequence_returns_no_alerts() -> None:
    """Assert an empty event list is handled."""
    assert run_sequence(MODEL, [], _dummy_classify) == []


def test_instance_key() -> None:
    """Assert key construction, including events without a resource."""
    assert instance_key({"resource_type": "Order", "resource_id": "1"}) == "Order:1"
    assert instance_key({"resource_type": None, "resource_id": None}) == "unscoped"
    assert instance_key({}) == "unscoped"
