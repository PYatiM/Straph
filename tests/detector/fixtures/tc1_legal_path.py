"""Test case 1 fixture: Legal multi-step checkout workflow for order 1001."""

import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft7Validator

ROOT: Path = Path(__file__).resolve().parents[3]
EVENT_SCHEMA_PATH: Path = ROOT / "contracts" / "event.schema.json"


def get_tc1_events() -> list[dict[str, Any]]:
    """Return seven schema-valid API events representing a legal checkout path.

    Sequence for order 1001:
    1. POST /cart/items (customer) -> 200
    2. POST /orders (customer) -> 201
    3. PUT /orders/1001/address (customer) -> 200
    4. POST /orders/1001/payment (customer) -> 200
    5. POST /payments/webhook (payment-gateway) -> 200
    6. POST /orders/1001/fulfill (warehouse) -> 200
    7. POST /orders/1001/ship (warehouse) -> 200
    """
    return [
        {
            "event_id": "00000001-0000-4000-8000-000000000001",
            "ts_received": "2026-10-29T10:00:00.000Z",
            "ts_completed": "2026-10-29T10:00:00.050Z",
            "subject": {
                "issuer": "straph-test",
                "sub": "customer-1001",
                "role": "customer",
            },
            "session_id": "sess-cust-1001",
            "token_id": "tok-cust-1001",
            "operation": {
                "method": "POST",
                "path_template": "/cart/items",
            },
            "resource_type": "Cart",
            "resource_id": "cart-1001",
            "status_code": 200,
        },
        {
            "event_id": "00000001-0000-4000-8000-000000000002",
            "ts_received": "2026-10-29T10:01:00.000Z",
            "ts_completed": "2026-10-29T10:01:00.050Z",
            "subject": {
                "issuer": "straph-test",
                "sub": "customer-1001",
                "role": "customer",
            },
            "session_id": "sess-cust-1001",
            "token_id": "tok-cust-1001",
            "operation": {
                "method": "POST",
                "path_template": "/orders",
            },
            "resource_type": "Order",
            "resource_id": "1001",
            "status_code": 201,
        },
        {
            "event_id": "00000001-0000-4000-8000-000000000003",
            "ts_received": "2026-10-29T10:02:00.000Z",
            "ts_completed": "2026-10-29T10:02:00.050Z",
            "subject": {
                "issuer": "straph-test",
                "sub": "customer-1001",
                "role": "customer",
            },
            "session_id": "sess-cust-1001",
            "token_id": "tok-cust-1001",
            "operation": {
                "method": "PUT",
                "path_template": "/orders/1001/address",
            },
            "resource_type": "Order",
            "resource_id": "1001",
            "status_code": 200,
        },
        {
            "event_id": "00000001-0000-4000-8000-000000000004",
            "ts_received": "2026-10-29T10:03:00.000Z",
            "ts_completed": "2026-10-29T10:03:00.050Z",
            "subject": {
                "issuer": "straph-test",
                "sub": "customer-1001",
                "role": "customer",
            },
            "session_id": "sess-cust-1001",
            "token_id": "tok-cust-1001",
            "operation": {
                "method": "POST",
                "path_template": "/orders/1001/payment",
            },
            "resource_type": "Order",
            "resource_id": "1001",
            "status_code": 200,
        },
        {
            "event_id": "00000001-0000-4000-8000-000000000005",
            "ts_received": "2026-10-29T10:04:00.000Z",
            "ts_completed": "2026-10-29T10:04:00.050Z",
            "subject": {
                "issuer": "straph-test",
                "sub": "payment-gateway",
                "role": "payment-gateway",
            },
            "session_id": "sess-webhook-gateway",
            "token_id": "tok-webhook-gateway",
            "operation": {
                "method": "POST",
                "path_template": "/payments/webhook",
            },
            "resource_type": "Order",
            "resource_id": "1001",
            "status_code": 200,
        },
        {
            "event_id": "00000001-0000-4000-8000-000000000006",
            "ts_received": "2026-10-29T10:05:00.000Z",
            "ts_completed": "2026-10-29T10:05:00.050Z",
            "subject": {
                "issuer": "straph-test",
                "sub": "warehouse-worker-1",
                "role": "warehouse",
            },
            "session_id": "sess-warehouse-worker",
            "token_id": "tok-warehouse-worker",
            "operation": {
                "method": "POST",
                "path_template": "/orders/1001/fulfill",
            },
            "resource_type": "Order",
            "resource_id": "1001",
            "status_code": 200,
        },
        {
            "event_id": "00000001-0000-4000-8000-000000000007",
            "ts_received": "2026-10-29T10:06:00.000Z",
            "ts_completed": "2026-10-29T10:06:00.050Z",
            "subject": {
                "issuer": "straph-test",
                "sub": "warehouse-worker-1",
                "role": "warehouse",
            },
            "session_id": "sess-warehouse-worker",
            "token_id": "tok-warehouse-worker",
            "operation": {
                "method": "POST",
                "path_template": "/orders/1001/ship",
            },
            "resource_type": "Order",
            "resource_id": "1001",
            "status_code": 200,
        },
    ]


def get_tc1_expected_alerts() -> list[dict[str, Any]]:
    """Return the expected detection alerts for TC1 (empty list for legal flow)."""
    return []


@pytest.mark.parametrize("event", get_tc1_events())
def test_each_tc1_event_valid_against_schema(event: dict[str, Any]) -> None:
    """Validate every TC1 event payload against contracts/event.schema.json."""
    schema: dict[str, Any] = json.loads(EVENT_SCHEMA_PATH.read_text(encoding="utf-8"))
    validator = Draft7Validator(schema)
    errors = list(validator.iter_errors(event))

    assert errors == [], (
        f"Validation failed for event {event['event_id']}: {[error.message for error in errors]}"
    )
