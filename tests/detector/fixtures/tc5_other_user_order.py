"""Test case 5 fixture: user-bravo cancels order 1005 owned by user-alpha (BOLA, rule R0)."""

from typing import Any


def get_tc5_events() -> list[dict[str, Any]]:
    """Return 2 schema-valid events: owner creates the order, another customer cancels it.

    Sequence for order 1005:
    1. POST /orders (user-alpha, customer) -> 201
    2. POST /orders/1005/cancel (user-bravo, customer) -> 200  (not the owner)
    """
    return [
        {
            "event_id": "00000005-0000-4000-8000-000000000001",
            "ts_received": "2026-10-29T14:00:00.000Z",
            "ts_completed": "2026-10-29T14:00:00.050Z",
            "session_id": "sess-user-alpha",
            "token_id": "tok-user-alpha",
            "identity": "user-alpha",
            "role": "customer",
            "method": "POST",
            "path_template": "/orders",
            "resource_type": "Order",
            "resource_id": "1005",
            "status": 201,
        },
        {
            "event_id": "00000005-0000-4000-8000-000000000002",
            "ts_received": "2026-10-29T14:01:00.000Z",
            "ts_completed": "2026-10-29T14:01:00.050Z",
            "session_id": "sess-user-bravo",
            "token_id": "tok-user-bravo",
            "identity": "user-bravo",
            "role": "customer",
            "method": "POST",
            "path_template": "/orders/1005/cancel",
            "resource_type": "Order",
            "resource_id": "1005",
            "status": 200,
        },
    ]


def get_tc5_expected_alerts() -> list[dict[str, Any]]:
    """Return the single expected R0 IDENTITY_MISMATCH alert for TC5.

    The primary violation is the ownership mismatch; ``secondary_label`` records the
    step transition computed for the cancel (the order reaches CANCELLED).
    """
    return [
        {
            "alert_id": "00000005-0000-4000-8000-0000000000a1",
            "event_id": "00000005-0000-4000-8000-000000000002",
            "timestamp": "2026-10-29T14:01:00.050Z",
            "label": "IDENTITY_MISMATCH",
            "rule": "R0",
            "secondary_label": "CANCELLED",
            "severity": "accepted",
            "subject": {"sub": "user-bravo"},
            "resource": {"type": "Order", "id": "1005"},
            "expected_path": ["POST /orders", "POST /orders/{id}/cancel"],
            "observed": {
                "operation": "POST /orders/{id}/cancel",
                "state": "CREATED",
                "status_code": 200,
            },
            "missing": [],
            "owasp_class": "BOLA",
            "flags": {"tainted": True, "late": False, "retry": False},
            "model_version": "checkout-v0",
            "rank_score": None,
        },
    ]
