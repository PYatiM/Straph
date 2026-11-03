"""Event stream grouper for partitioning and sorting events per business object."""

from __future__ import annotations

from typing import Any


def group_events_by_object(
    events: list[dict[str, Any]],
    discard_null_resource_id: bool = True,
) -> dict[tuple[str, str], list[dict[str, Any]]]:
    """Group, deduplicate, and chronologically sort events per business object instance.

    Args:
        events: List of raw event dictionaries matching contracts/event.schema.json.
        discard_null_resource_id: If True, events without a valid resource_id are omitted.

    Returns:
        Mapping from composite key (resource_type, resource_id) to the ordered list
        of unique event dictionaries for that entity.
    """
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    seen_event_ids: set[str] = set()

    for ev in events:
        if not isinstance(ev, dict):
            continue

        event_id = ev.get("event_id")
        if event_id:
            if event_id in seen_event_ids:
                continue
            seen_event_ids.add(event_id)

        res_type = ev.get("resource_type")
        res_id = ev.get("resource_id")

        if discard_null_resource_id and (res_id is None or res_id == ""):
            continue

        if res_type is None or res_type == "":
            continue

        key = (str(res_type), str(res_id))
        grouped.setdefault(key, []).append(ev)

    # Sort each per-object stream chronologically by completion timestamp
    for key, ev_list in grouped.items():
        ev_list.sort(key=lambda x: str(x.get("ts_completed") or x.get("ts_received") or ""))

    return grouped
