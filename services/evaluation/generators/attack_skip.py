"""Attack Generator 1: Skip Payment (skip_payment).

Generates schema-valid API events and GroundTruthLabel records for an order
where warehouse fulfillment is called immediately after setting the shipping
address, skipping payment initiation and webhook confirmation.
"""

from __future__ import annotations

import random
import uuid
from typing import Any

from services.evaluation.labels import ExpectedLabel, ExpectedRule, GroundTruthLabel

SCENARIO_ID = "ATTACK_1_SKIP_PAYMENT"
MISSING_PAYMENT_OPS: list[str] = [
    "POST /orders/{id}/payment",
    "POST /payments/webhook",
]


def _seeded_uuid4(rng: random.Random) -> str:
    """Generate a deterministic RFC-4122 UUID4 string from a seeded RNG."""
    return str(uuid.UUID(int=rng.getrandbits(128), version=4))


def generate_skip_payment_attack(
    order_id: str,
    seed: int = 42,
) -> tuple[list[dict[str, Any]], list[GroundTruthLabel]]:
    """Generate Attack 1 (skip_payment) event sequence and ground-truth labels.

    Sequence:
    1. POST /orders -> 201 (benign)
    2. PUT /orders/{id}/address -> 200 (benign)
    3. POST /orders/{id}/fulfill -> 200 (attack: skips payment and webhook)

    Args:
        order_id: Target Order resource identifier.
        seed: Deterministic seed for event IDs and timestamps.

    Returns:
        Tuple of (events, labels) where events conform to contracts/event.schema.json
        and labels are GroundTruthLabel instances.
    """
    if not isinstance(order_id, str) or not order_id.strip():
        raise ValueError("order_id must be a non-empty string.")

    clean_order_id = order_id.strip()
    rng = random.Random(f"skip_payment:{clean_order_id}:{seed}")

    customer_sub = f"customer-{clean_order_id}"
    customer_session = f"sess-cust-{clean_order_id}"
    customer_token = f"tok-cust-{clean_order_id}"

    warehouse_sub = "warehouse-worker-1"
    warehouse_session = "sess-warehouse-worker"
    warehouse_token = "tok-warehouse-worker"

    event_1_id = _seeded_uuid4(rng)
    event_2_id = _seeded_uuid4(rng)
    event_3_id = _seeded_uuid4(rng)

    events: list[dict[str, Any]] = [
        {
            "event_id": event_1_id,
            "ts_received": "2026-10-29T11:00:00.000Z",
            "ts_completed": "2026-10-29T11:00:00.050Z",
            "subject": {
                "issuer": "straph-eval",
                "sub": customer_sub,
                "role": "customer",
            },
            "session_id": customer_session,
            "token_id": customer_token,
            "operation": {
                "method": "POST",
                "path_template": "/orders",
            },
            "resource_type": "Order",
            "resource_id": clean_order_id,
            "status_code": 201,
        },
        {
            "event_id": event_2_id,
            "ts_received": "2026-10-29T11:01:00.000Z",
            "ts_completed": "2026-10-29T11:01:00.050Z",
            "subject": {
                "issuer": "straph-eval",
                "sub": customer_sub,
                "role": "customer",
            },
            "session_id": customer_session,
            "token_id": customer_token,
            "operation": {
                "method": "PUT",
                "path_template": "/orders/{id}/address",
            },
            "resource_type": "Order",
            "resource_id": clean_order_id,
            "status_code": 200,
        },
        {
            "event_id": event_3_id,
            "ts_received": "2026-10-29T11:02:00.000Z",
            "ts_completed": "2026-10-29T11:02:00.050Z",
            "subject": {
                "issuer": "straph-eval",
                "sub": warehouse_sub,
                "role": "warehouse",
            },
            "session_id": warehouse_session,
            "token_id": warehouse_token,
            "operation": {
                "method": "POST",
                "path_template": "/orders/{id}/fulfill",
            },
            "resource_type": "Order",
            "resource_id": clean_order_id,
            "status_code": 200,
        },
    ]

    labels: list[GroundTruthLabel] = [
        GroundTruthLabel(
            event_id=event_1_id,
            is_violation=False,
            expected_label=ExpectedLabel.BENIGN,
            expected_rule=None,
            missing_ops=[],
            scenario_id=SCENARIO_ID,
            seed=seed,
        ),
        GroundTruthLabel(
            event_id=event_2_id,
            is_violation=False,
            expected_label=ExpectedLabel.BENIGN,
            expected_rule=None,
            missing_ops=[],
            scenario_id=SCENARIO_ID,
            seed=seed,
        ),
        GroundTruthLabel(
            event_id=event_3_id,
            is_violation=True,
            expected_label=ExpectedLabel.SKIPPED,
            expected_rule=ExpectedRule.R2,
            missing_ops=list(MISSING_PAYMENT_OPS),
            scenario_id=SCENARIO_ID,
            seed=seed,
        ),
    ]

    return events, labels
