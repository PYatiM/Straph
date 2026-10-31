"""Test case 2 fixture: warehouse fulfills order 1002 skipping payment and webhook."""

from typing import Any


def get_tc2_events() -> list[dict[str, Any]]:
    """Return 3 schema-valid API events where payment and webhook are skipped.

    Sequence for order 1002:
    1. POST /orders (customer) -> 201
    2. PUT /orders/1002/address (customer) -> 200
    3. POST /orders/1002/fulfill (warehouse) -> 200 (skips payment and webhook)
    """
    return [
        {
            "event_id": "00000002-0000-4000-8000-000000000001",
            "ts_received": "2026-10-29T11:00:00.000Z",
            "ts_completed": "2026-10-29T11:00:00.050Z",
            "subject": {
                "issuer": "straph-test",
                "sub": "customer-1002",
                "role": "customer",
            },
            "session_id": "sess-cust-1002",
            "token_id": "tok-cust-1002",
            "operation": {
                "method": "POST",
                "path_template": "/orders",
            },
            "resource_type": "Order",
            "resource_id": "1002",
            "status_code": 201,
        },
        {
            "event_id": "00000002-0000-4000-8000-000000000002",
            "ts_received": "2026-10-29T11:01:00.000Z",
            "ts_completed": "2026-10-29T11:01:00.050Z",
            "subject": {
                "issuer": "straph-test",
                "sub": "customer-1002",
                "role": "customer",
            },
            "session_id": "sess-cust-1002",
            "token_id": "tok-cust-1002",
            "operation": {
                "method": "PUT",
                "path_template": "/orders/1002/address",
            },
            "resource_type": "Order",
            "resource_id": "1002",
            "status_code": 200,
        },
        {
            "event_id": "00000002-0000-4000-8000-000000000003",
            "ts_received": "2026-10-29T11:02:00.000Z",
            "ts_completed": "2026-10-29T11:02:00.050Z",
            "subject": {
                "issuer": "straph-test",
                "sub": "warehouse-worker-1",
                "role": "warehouse",
            },
            "session_id": "sess-warehouse-worker",
            "token_id": "tok-warehouse-worker",
            "operation": {
                "method": "POST",
                "path_template": "/orders/1002/fulfill",
            },
            "resource_type": "Order",
            "resource_id": "1002",
            "status_code": 200,
        },
    ]


def get_tc2_expected_alerts() -> list[dict[str, Any]]:
    """Return the single expected R2 SKIPPED alert (accepted, tainted) for TC2."""
    return [
        {
            "alert_id": "00000002-0000-4000-8000-0000000000a1",
            "event_id": "00000002-0000-4000-8000-000000000003",
            "timestamp": "2026-10-29T11:02:00.050Z",
            "label": "SKIPPED",
            "rule": "R2",
            "severity": "accepted",
            "subject": {
                "identity": "warehouse-worker-1",
                "role": "warehouse",
            },
            "resource": {"type": "Order", "id": "1002"},
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
