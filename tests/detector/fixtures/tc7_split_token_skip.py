"""Test case 7 fixture: token rotation does not evade per-object state tracking (order 1007)."""

from typing import Any

_ORDER_ID: str = "1007"
_USER_A: str = "user-a"
_USER_A_ROLE: str = "admin"
_TOKEN_1: str = "tok-user-a-1"
_TOKEN_2: str = "tok-user-a-2"
_SESSION_1: str = "sess-user-a-1"
_SESSION_2: str = "sess-user-a-2"


def _event(
    seq: int,
    minute: int,
    method: str,
    path_template: str,
    status: int,
    token_id: str,
    session_id: str,
) -> dict[str, Any]:
    """Build one schema-valid TC7 event for User A on order 1007."""
    return {
        "event_id": f"00000007-0000-4000-8000-{seq:012d}",
        "ts_received": f"2026-10-29T16:{minute:02d}:00.000Z",
        "ts_completed": f"2026-10-29T16:{minute:02d}:00.050Z",
        "subject": {
            "issuer": "straph-test",
            "sub": _USER_A,
            "role": _USER_A_ROLE,
        },
        "session_id": session_id,
        "token_id": token_id,
        "operation": {
            "method": method,
            "path_template": path_template,
        },
        "resource_type": "Order",
        "resource_id": _ORDER_ID,
        "status_code": status,
    }


def get_tc7_events() -> list[dict[str, Any]]:
    """Return 3 schema-valid events by one identity across two tokens/sessions.

    Sequence for order 1007:
    1. POST /orders -> 201 (token 1, session 1)
    2. PUT /orders/1007/address -> 200 (token 1, session 1)
    3. POST /orders/1007/fulfill -> 200 (token 2, session 2; payment and webhook skipped)
    """
    return [
        _event(1, 0, "POST", "/orders", 201, _TOKEN_1, _SESSION_1),
        _event(
            2,
            1,
            "PUT",
            "/orders/1007/address",
            200,
            _TOKEN_1,
            _SESSION_1,
        ),
        _event(
            3,
            2,
            "POST",
            "/orders/1007/fulfill",
            200,
            _TOKEN_2,
            _SESSION_2,
        ),
    ]


def get_tc7_expected_alerts() -> list[dict[str, Any]]:
    """Return the single expected R2 SKIPPED alert for TC7.

    State is tracked per resource_type:resource_id (Order:1007), not per token or
    session, so the rotated token on the final event still triggers the skipped-step alert.
    """
    return [
        {
            "alert_id": "00000007-0000-4000-8000-0000000000a1",
            "event_id": "00000007-0000-4000-8000-000000000003",
            "timestamp": "2026-10-29T16:02:00.050Z",
            "label": "SKIPPED",
            "rule": "R2",
            "severity": "accepted",
            "subject": {
                "identity": _USER_A,
                "role": _USER_A_ROLE,
                "token_id": _TOKEN_2,
                "session_id": _SESSION_2,
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
                "state": "ADDRESS_SET",
                "status_code": 200,
            },
            "missing": [
                "POST /orders/{id}/payment",
                "POST /payments/webhook",
            ],
            "owasp_class": "BLA6",
            "flags": {
                "tainted": True,
                "late": False,
                "retry": False,
            },
            "model_version": "checkout-v0",
            "rank_score": None,
        },
    ]
