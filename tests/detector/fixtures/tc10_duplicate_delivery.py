"""Test case 10 fixture: Kafka at-least-once duplicate delivery on order 1010 (no alerts)."""

import copy
from typing import Any


def _event(seq: int, minute: int, method: str, path_template: str, status: int) -> dict[str, Any]:
    """Build one schema-valid TC10 event for customer-1010 on order 1010."""
    return {
        "event_id": f"00000010-0000-4000-8000-{seq:012d}",
        "ts_received": f"2026-10-29T19:{minute:02d}:00.000Z",
        "ts_completed": f"2026-10-29T19:{minute:02d}:00.050Z",
        "session_id": "sess-cust-1010",
        "token_id": "tok-cust-1010",
        "identity": "customer-1010",
        "role": "customer",
        "method": method,
        "path_template": path_template,
        "resource_type": "Order",
        "resource_id": "1010",
        "status": status,
    }


def get_tc10_events() -> list[dict[str, Any]]:
    """Return 4 schema-valid events where the last is a verbatim re-delivery of the 3rd.

    Sequence for order 1010:
    1. POST /cart/items -> 200
    2. POST /orders -> 201
    3. PUT /orders/1010/address -> 200   (state: ADDRESS_SET)
    4. Duplicate of #3: identical event_id, timestamps and payload (Kafka re-delivery)
    """
    cart: dict[str, Any] = _event(1, 0, "POST", "/cart/items", 200)
    cart["resource_type"] = "Cart"
    cart["resource_id"] = "cart-1010"
    address: dict[str, Any] = _event(3, 2, "PUT", "/orders/1010/address", 200)
    return [
        cart,
        _event(2, 1, "POST", "/orders", 201),
        address,
        copy.deepcopy(address),
    ]


def get_tc10_expected_alerts() -> list[dict[str, Any]]:
    """Return the expected alerts for TC10 (empty: the detector deduplicates by event_id).

    Without deduplication the repeated address edit would be a legal self-loop anyway, but
    the contract is that a re-delivered event_id is processed at most once.
    """
    return []
