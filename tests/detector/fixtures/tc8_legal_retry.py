"""Test case 8 fixture: idempotent payment retry on order 1008 (benign, no alerts)."""

from typing import Any

_ORDER_ID: str = "1008"
_IDEMPOTENCY_KEY: str = "idemp-999"


def _event(
    seq: int,
    minute: int,
    identity: str,
    role: str,
    method: str,
    path_template: str,
    status: int,
    resource_type: str = "Order",
    resource_id: str = _ORDER_ID,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Build one schema-valid event for TC8."""
    event: dict[str, Any] = {
        "event_id": f"00000008-0000-4000-8000-{seq:012d}",
        "ts_received": f"2026-10-29T17:{minute:02d}:00.000Z",
        "ts_completed": f"2026-10-29T17:{minute:02d}:00.050Z",
        "session_id": f"sess-{identity}",
        "token_id": f"tok-{identity}",
        "identity": identity,
        "role": role,
        "method": method,
        "path_template": path_template,
        "resource_type": resource_type,
        "resource_id": resource_id,
        "status": status,
    }
    if idempotency_key is not None:
        event["idempotency_key"] = idempotency_key
    return event


def get_tc8_events() -> list[dict[str, Any]]:
    """Return 5 schema-valid events: path to PAYMENT_PENDING, a failed and a retried payment.

    Sequence for order 1008:
    1. POST /cart/items (customer-1008) -> 200
    2. POST /orders (customer-1008) -> 201
    3. PUT /orders/1008/address (customer-1008) -> 200   (state: PAYMENT_PENDING)
    4. POST /orders/1008/payment [idemp-999] -> 500      (failed, state unchanged)
    5. POST /orders/1008/payment [idemp-999] -> 200      (successful retry, legal advance)
    """
    c: str = "customer-1008"
    return [
        _event(1, 0, c, "customer", "POST", "/cart/items", 200, "Cart", "cart-1008"),
        _event(2, 1, c, "customer", "POST", "/orders", 201),
        _event(3, 2, c, "customer", "PUT", "/orders/1008/address", 200),
        _event(
            4,
            3,
            c,
            "customer",
            "POST",
            "/orders/1008/payment",
            500,
            idempotency_key=_IDEMPOTENCY_KEY,
        ),
        _event(
            5,
            4,
            c,
            "customer",
            "POST",
            "/orders/1008/payment",
            200,
            idempotency_key=_IDEMPOTENCY_KEY,
        ),
    ]


def get_tc8_expected_alerts() -> list[dict[str, Any]]:
    """Return the expected alerts for TC8 (empty: idempotent retries are benign).

    The 500 response is not accepted, so it never enters history and the retry is a
    legal advance rather than an R1 replay.
    """
    return []
