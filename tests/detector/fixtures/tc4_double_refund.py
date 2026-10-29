"""Test case 4 fixture: support agent refunds order 1004 twice (replay of one-shot op)."""

from typing import Any

_ORDER_ID: str = "1004"


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
) -> dict[str, Any]:
    """Build one schema-valid event for TC4."""
    return {
        "event_id": f"00000004-0000-4000-8000-{seq:012d}",
        "ts_received": f"2026-10-29T13:{minute:02d}:00.000Z",
        "ts_completed": f"2026-10-29T13:{minute:02d}:00.050Z",
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


def get_tc4_events() -> list[dict[str, Any]]:
    """Return 10 schema-valid events: legal path to DELIVERED, then two refunds.

    Sequence for order 1004:
    1. POST /cart/items (customer) -> 200
    2. POST /orders (customer) -> 201
    3. PUT /orders/1004/address (customer) -> 200
    4. POST /orders/1004/payment (customer) -> 200
    5. POST /payments/webhook (payment-gateway) -> 200
    6. POST /orders/1004/fulfill (warehouse) -> 200
    7. POST /orders/1004/ship (warehouse) -> 200
    8. POST /orders/1004/deliver (warehouse) -> 200   (state: DELIVERED)
    9. POST /orders/1004/refund (support) -> 200      (legal, state: REFUNDED, one-shot)
    10. POST /orders/1004/refund (support) -> 200     (replay, accepted)
    """
    cust: str = "customer-1004"
    wh: str = "warehouse-worker-1"
    sup: str = "support-agent-1"
    return [
        _event(1, 0, cust, "customer", "POST", "/cart/items", 200, "Cart", "cart-1004"),
        _event(2, 1, cust, "customer", "POST", "/orders", 201),
        _event(3, 2, cust, "customer", "PUT", "/orders/1004/address", 200),
        _event(4, 3, cust, "customer", "POST", "/orders/1004/payment", 200),
        _event(5, 4, "payment-gateway", "payment-gateway", "POST", "/payments/webhook", 200),
        _event(6, 5, wh, "warehouse", "POST", "/orders/1004/fulfill", 200),
        _event(7, 6, wh, "warehouse", "POST", "/orders/1004/ship", 200),
        _event(8, 7, wh, "warehouse", "POST", "/orders/1004/deliver", 200),
        _event(9, 8, sup, "support", "POST", "/orders/1004/refund", 200),
        _event(10, 9, sup, "support", "POST", "/orders/1004/refund", 200),
    ]


def get_tc4_expected_alerts() -> list[dict[str, Any]]:
    """Return the single expected R1 REPLAYED alert (accepted, tainted) for TC4."""
    return [
        {
            "alert_id": "00000004-0000-4000-8000-0000000000a1",
            "event_id": "00000004-0000-4000-8000-000000000010",
            "timestamp": "2026-10-29T13:09:00.050Z",
            "label": "REPLAYED",
            "rule": "R1",
            "severity": "accepted",
            "subject": {"identity": "support-agent-1", "role": "support"},
            "resource": {"type": "Order", "id": _ORDER_ID},
            "expected_path": [
                "POST /orders",
                "PUT /orders/{id}/address",
                "POST /orders/{id}/payment",
                "POST /payments/webhook",
                "POST /orders/{id}/fulfill",
                "POST /orders/{id}/ship",
                "POST /orders/{id}/deliver",
                "POST /orders/{id}/refund",
            ],
            "observed": {
                "operation": "POST /orders/{id}/refund",
                "state": "REFUNDED",
                "status_code": 200,
            },
            "missing": [],
            "owasp_class": "BLA6",
            "flags": {"tainted": True, "late": False, "retry": False},
            "model_version": "checkout-v0",
            "rank_score": None,
        },
    ]
