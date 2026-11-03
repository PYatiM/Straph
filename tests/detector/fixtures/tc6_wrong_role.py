"""Test case 6 fixture: a customer calls the warehouse-only fulfill operation on order 1006."""

from typing import Any

_ORDER_ID: str = "1006"
_USER_C: str = "user-charlie"


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
    """Build one schema-valid event for TC6."""
    return {
        "event_id": f"00000006-0000-4000-8000-{seq:012d}",
        "ts_received": f"2026-10-29T15:{minute:02d}:00.000Z",
        "ts_completed": f"2026-10-29T15:{minute:02d}:00.050Z",
        "subject": {
            "issuer": "straph-test",
            "sub": identity,
            "role": role,
        },
        "session_id": f"sess-{identity}",
        "token_id": f"tok-{identity}",
        "operation": {
            "method": method,
            "path_template": path_template,
        },
        "resource_type": resource_type,
        "resource_id": resource_id,
        "status_code": status,
    }


def get_tc6_events() -> list[dict[str, Any]]:
    """Return 6 schema-valid events: legal path to PAID, then a customer-role fulfill.

    Sequence for order 1006:
    1. POST /cart/items (user-charlie, customer) -> 200
    2. POST /orders (user-charlie, customer) -> 201
    3. PUT /orders/1006/address (user-charlie, customer) -> 200
    4. POST /orders/1006/payment (user-charlie, customer) -> 200
    5. POST /payments/webhook (payment-gateway) -> 200 (state: PAID)
    6. POST /orders/1006/fulfill (user-charlie, customer) -> 200 (requires warehouse role)
    """
    c: str = _USER_C

    return [
        _event(
            1,
            0,
            c,
            "customer",
            "POST",
            "/cart/items",
            200,
            "Cart",
            "cart-1006",
        ),
        _event(2, 1, c, "customer", "POST", "/orders", 201),
        _event(3, 2, c, "customer", "PUT", "/orders/1006/address", 200),
        _event(4, 3, c, "customer", "POST", "/orders/1006/payment", 200),
        _event(
            5,
            4,
            "payment-gateway",
            "payment-gateway",
            "POST",
            "/payments/webhook",
            200,
        ),
        _event(6, 5, c, "customer", "POST", "/orders/1006/fulfill", 200),
    ]


def get_tc6_expected_alerts() -> list[dict[str, Any]]:
    """Return the single expected R0 IDENTITY_MISMATCH alert for TC6.

    The state transition itself is legal from PAID, so ``secondary_label`` is
    FULFILLED; the primary violation is the role lacking permission for fulfill.
    """
    return [
        {
            "alert_id": "00000006-0000-4000-8000-0000000000a1",
            "event_id": "00000006-0000-4000-8000-000000000006",
            "timestamp": "2026-10-29T15:05:00.050Z",
            "label": "IDENTITY_MISMATCH",
            "rule": "R0",
            "secondary_label": "FULFILLED",
            "severity": "accepted",
            "subject": {
                "identity": _USER_C,
                "role": "customer",
            },
            "resource": {
                "type": "Order",
                "id": _ORDER_ID,
            },
            "expected_path": [
                "POST /orders",
                "PUT /orders/{id}/address",
                "POST /orders/{id}/payment",
                "POST /payments/webhook",
                "POST /orders/{id}/fulfill",
            ],
            "observed": {
                "operation": "POST /orders/{id}/fulfill",
                "state": "PAID",
                "status_code": 200,
            },
            "missing": [],
            "owasp_class": "BFLA",
            "flags": {
                "tainted": True,
                "late": False,
                "retry": False,
            },
            "model_version": "checkout-v0",
            "rank_score": None,
        },
    ]
