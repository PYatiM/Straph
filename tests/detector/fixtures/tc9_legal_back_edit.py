"""Test case 9 fixture: legal address self-loop before payment on order 1009 (no alerts)."""

from typing import Any


def _event(
    seq: int,
    minute: int,
    method: str,
    path_template: str,
    status: int,
) -> dict[str, Any]:
    """Build one schema-valid TC9 event for customer-1009 on order 1009."""
    return {
        "event_id": f"00000009-0000-4000-8000-{seq:012d}",
        "ts_received": f"2026-10-29T18:{minute:02d}:00.000Z",
        "ts_completed": f"2026-10-29T18:{minute:02d}:00.050Z",
        "subject": {
            "issuer": "straph-test",
            "sub": "customer-1009",
            "role": "customer",
        },
        "session_id": "sess-cust-1009",
        "token_id": "tok-cust-1009",
        "operation": {
            "method": method,
            "path_template": path_template,
        },
        "resource_type": "Order",
        "resource_id": "1009",
        "status_code": status,
    }


def get_tc9_events() -> list[dict[str, Any]]:
    """Return 3 schema-valid events: create, set address, then re-edit address.

    Sequence for order 1009:
    1. POST /orders -> 201
    2. PUT /orders/1009/address -> 200 (state: ADDRESS_SET)
    3. PUT /orders/1009/address -> 200 (legal self-loop: ADDRESS_SET -> ADDRESS_SET)
    """
    return [
        _event(1, 0, "POST", "/orders", 201),
        _event(2, 1, "PUT", "/orders/1009/address", 200),
        _event(3, 2, "PUT", "/orders/1009/address", 200),
    ]


def get_tc9_expected_alerts() -> list[dict[str, Any]]:
    """Return no alerts: repeating the address update is a legal self-loop.

    The address operation is not one-shot and is valid from ADDRESS_SET, so repeating it
    before payment must not raise R1 or R3.
    """
    return []
