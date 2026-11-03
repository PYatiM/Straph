"""Unit tests for session grouper partitioning event streams."""

from __future__ import annotations

from typing import Any

from services.model_builder.session_grouper import group_events_by_object


def test_interleaved_events_separated_and_sorted() -> None:
    """Verify interleaved events from orders 1001 and 1002 partition into distinct chronologically sorted streams."""
    events: list[dict[str, Any]] = [
        # Order 1002: step 2 (late timestamp)
        {
            "event_id": "ev-1002-2",
            "resource_type": "Order",
            "resource_id": "1002",
            "ts_completed": "2026-10-13T10:00:05.000Z",
            "operation": {"method": "PUT", "path_template": "/orders/{id}/address"},
        },
        # Order 1001: step 1 (early timestamp)
        {
            "event_id": "ev-1001-1",
            "resource_type": "Order",
            "resource_id": "1001",
            "ts_completed": "2026-10-13T10:00:01.000Z",
            "operation": {"method": "POST", "path_template": "/orders"},
        },
        # Order 1002: step 1 (intermediate timestamp)
        {
            "event_id": "ev-1002-1",
            "resource_type": "Order",
            "resource_id": "1002",
            "ts_completed": "2026-10-13T10:00:02.000Z",
            "operation": {"method": "POST", "path_template": "/orders"},
        },
        # Order 1001: step 2 (intermediate timestamp)
        {
            "event_id": "ev-1001-2",
            "resource_type": "Order",
            "resource_id": "1001",
            "ts_completed": "2026-10-13T10:00:03.000Z",
            "operation": {"method": "PUT", "path_template": "/orders/{id}/address"},
        },
        # Order 1001: step 3 (final timestamp)
        {
            "event_id": "ev-1001-3",
            "resource_type": "Order",
            "resource_id": "1001",
            "ts_completed": "2026-10-13T10:00:06.000Z",
            "operation": {"method": "POST", "path_template": "/orders/{id}/payment"},
        },
    ]

    grouped = group_events_by_object(events)

    assert set(grouped.keys()) == {("Order", "1001"), ("Order", "1002")}

    order_1001_events = grouped[("Order", "1001")]
    assert len(order_1001_events) == 3
    assert [e["event_id"] for e in order_1001_events] == [
        "ev-1001-1",
        "ev-1001-2",
        "ev-1001-3",
    ]

    order_1002_events = grouped[("Order", "1002")]
    assert len(order_1002_events) == 2
    assert [e["event_id"] for e in order_1002_events] == [
        "ev-1002-1",
        "ev-1002-2",
    ]


def test_events_with_identical_event_id_deduplicated() -> None:
    """Verify duplicate event_id entries are discarded on ingestion."""
    events: list[dict[str, Any]] = [
        {
            "event_id": "duplicate-uuid-1",
            "resource_type": "Order",
            "resource_id": "2001",
            "ts_completed": "2026-10-13T12:00:00.000Z",
            "operation": {"method": "POST", "path_template": "/orders"},
        },
        {
            "event_id": "duplicate-uuid-1",
            "resource_type": "Order",
            "resource_id": "2001",
            "ts_completed": "2026-10-13T12:00:00.000Z",
            "operation": {"method": "POST", "path_template": "/orders"},
        },
        {
            "event_id": "distinct-uuid-2",
            "resource_type": "Order",
            "resource_id": "2001",
            "ts_completed": "2026-10-13T12:00:05.000Z",
            "operation": {"method": "PUT", "path_template": "/orders/{id}/address"},
        },
    ]

    grouped = group_events_by_object(events)
    stream = grouped[("Order", "2001")]
    assert len(stream) == 2
    assert [e["event_id"] for e in stream] == ["duplicate-uuid-1", "distinct-uuid-2"]


def test_null_resource_id_discarded() -> None:
    """Verify events missing resource_id are excluded when discard_null_resource_id is True."""
    events: list[dict[str, Any]] = [
        {
            "event_id": "cart-event",
            "resource_type": "Order",
            "resource_id": None,
            "ts_completed": "2026-10-13T12:00:00.000Z",
            "operation": {"method": "POST", "path_template": "/cart/items"},
        },
        {
            "event_id": "empty-id-event",
            "resource_type": "Order",
            "resource_id": "",
            "ts_completed": "2026-10-13T12:00:01.000Z",
            "operation": {"method": "POST", "path_template": "/cart/items"},
        },
        {
            "event_id": "valid-order-event",
            "resource_type": "Order",
            "resource_id": "3001",
            "ts_completed": "2026-10-13T12:00:02.000Z",
            "operation": {"method": "POST", "path_template": "/orders"},
        },
    ]

    grouped = group_events_by_object(events, discard_null_resource_id=True)
    assert list(grouped.keys()) == [("Order", "3001")]
    assert len(grouped[("Order", "3001")]) == 1
