"""Test case 3 fixture: customer edits address of order 1003 after it has shipped."""

from typing import Any

_ORDER_ID: str = "1003"


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
    """Build one schema-valid event for TC3."""
    session: str = f"sess-{identity}"
    return {
        "event_id": f"00000003-0000-4000-8000-{seq:012d}",
        "ts_received": f"2026-10-29T12:{minute:02d}:00.000Z",
        "ts_completed": f"2026-10-29T12:{minute:02d}:00.050Z",
        "session_id": session,
        "token_id": f"tok-{identity}",
        "identity": identity,
        "role": role,
        "method": method,
        "path_template": path_template,
        "resource_type": resource_type,
        "resource_id": resource_id,
        "status": status,
    }


def get_tc3_events() -> list[dict[str, Any]]:
    """Return 8 schema-valid events: the TC1 legal path to SHIPPED, then an address edit.

    Sequence for order 1003:
    1. POST /cart/items (customer) -> 200
    2. POST /orders (customer) -> 201
    3. PUT /orders/1003/address (customer) -> 200
    4. POST /orders/1003/payment (customer) -> 200
    5. POST /payments/webhook (payment-gateway) -> 200
    6. POST /orders/1003/fulfill (warehouse) -> 200
    7. POST /orders/1003/ship (warehouse) -> 200   (state: SHIPPED)
    8. PUT /orders/1003/address (customer) -> 200  (out of order, accepted)
    """
    cust: str = "customer-1003"
    wh: str = "warehouse-worker-1"
    return [
        _event(1, 0, cust, "customer", "POST", "/cart/items", 200, "Cart", "cart-1003"),
        _event(2, 1, cust, "customer", "POST", "/orders", 201),
        _event(3, 2, cust, "customer", "PUT", "/orders/1003/address", 200),
        _event(4, 3, cust, "customer", "POST", "/orders/1003/payment", 200),
        _event(5, 4, "payment-gateway", "payment-gateway", "POST", "/payments/webhook", 200),
        _event(6, 5, wh, "warehouse", "POST", "/orders/1003/fulfill", 200),
        _event(7, 6, wh, "warehouse", "POST", "/orders/1003/ship", 200),
        _event(8, 7, cust, "customer", "PUT", "/orders/1003/address", 200),
    ]


def get_tc3_expected_alerts() -> list[dict[str, Any]]:
    """Return the single expected R3 OUT_OF_ORDER alert (accepted, tainted) for TC3."""
    return [
        {
            "alert_id": "00000003-0000-4000-8000-0000000000a1",
            "event_id": "00000003-0000-4000-8000-000000000008",
            "timestamp": "2026-10-29T12:07:00.050Z",
            "label": "OUT_OF_ORDER",
            "rule": "R3",
            "severity": "accepted",
            "subject": {"identity": "customer-1003", "role": "customer"},
            "resource": {"type": "Order", "id": _ORDER_ID},
            "expected_path": [
                "POST /orders",
                "PUT /orders/{id}/address",
                "POST /orders/{id}/payment",
                "POST /payments/webhook",
                "POST /orders/{id}/fulfill",
                "POST /orders/{id}/ship",
            ],
            "observed": {
                "operation": "PUT /orders/{id}/address",
                "state": "SHIPPED",
                "status_code": 200,
            },
            "missing": [],
            "owasp_class": "BLA6",
            "flags": {"tainted": True, "late": False, "retry": False},
            "model_version": "checkout-v0",
            "rank_score": None,
        },
    ]
