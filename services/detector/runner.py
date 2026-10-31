"""Offline sequence runner: feeds events through a classifier with in-memory instance state.

No Kafka, Redis or Neo4j is required, so fixtures can be replayed in plain unit tests.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

UNSCOPED_KEY: str = "unscoped"
"""Instance key used for events that carry no resource type/id."""


class AlertRaised(Exception):
    """Raised by a classifier to emit an alert; the runner collects ``alert``."""

    def __init__(self, alert: dict[str, Any]) -> None:
        super().__init__(alert.get("label", "alert"))
        self.alert: dict[str, Any] = alert


@dataclass
class InstanceState:
    """In-memory state record for one business object instance (``type:id``)."""

    key: str
    state: str | None = None
    history: list[dict[str, Any]] = field(default_factory=list)
    event_count: int = 0
    last_event_id: str | None = None
    tainted: bool = False


ClassifyFn = Callable[[InstanceState, dict[str, Any], dict[str, Any]], dict[str, Any] | None]


def instance_key(event: dict[str, Any]) -> str:
    """Return the ``resource_type:resource_id`` key for an event."""
    resource_type: str | None = event.get("resource_type")
    resource_id: str | None = event.get("resource_id")
    if not resource_type or not resource_id:
        return UNSCOPED_KEY
    return f"{resource_type}:{resource_id}"


def run_sequence(
    model: dict[str, Any],
    events: list[dict[str, Any]],
    classify_fn: ClassifyFn,
) -> list[dict[str, Any]]:
    """Replay ``events`` in order and return every alert raised by ``classify_fn``.

    For each event the runner looks up (or creates) the instance record, calls
    ``classify_fn(instance, event, model)`` with the record as it stood *before* the event,
    and then updates the record: ``event_count`` and ``last_event_id`` always, and the event
    is appended to ``history`` when its status is 2xx. ``classify_fn`` may mutate
    ``instance`` (e.g. advance ``state`` or set ``tainted``).

    A classifier signals an alert either by returning an alert dict or by raising
    :class:`AlertRaised`; returning ``None`` means the event is benign. The record is still
    updated after a raised alert.
    """
    instances: dict[str, InstanceState] = {}
    alerts: list[dict[str, Any]] = []

    for event in events:
        key: str = instance_key(event)
        instance: InstanceState = instances.setdefault(key, InstanceState(key=key))

        try:
            alert: dict[str, Any] | None = classify_fn(instance, event, model)
        except AlertRaised as raised:
            alert = raised.alert
        if alert is not None:
            alerts.append(alert)

        instance.event_count += 1
        instance.last_event_id = event["event_id"]
        if 200 <= event["status_code"] < 300:
            instance.history.append(event)

    return alerts
