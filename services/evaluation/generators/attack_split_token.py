"""Attack Generator 6: Split-Token Payment Skip across rotated sessions.

Generates schema-valid API events and GroundTruthLabel records for an order
where the same user subject creates the order and sets the address using Token 1
(Session 1), then rotates to Token 2 (Session 2) to invoke POST /orders/{id}/fulfill
directly without paying. Per-object instance tracking (Order:{id}) catches the
R2 SKIPPED violation regardless of session/token rotation.
"""

from __future__ import annotations

import random
import uuid
from typing import Any

from services.evaluation.labels import ExpectedLabel, ExpectedRule, GroundTruthLabel

SCENARIO_ID = "ATTACK_6_SPLIT_TOKEN_SKIP"
MISSING_PAYMENT_OPS: list[str] = [
    "POST /orders/{id}/payment",
    "POST /payments/webhook",
]


def _seeded_uuid4(rng: random.Random) -> str:
    """Generate a deterministic RFC-4122 UUID4 string from a seeded RNG."""
    return str(uuid.UUID(int=rng.getrandbits(128), version=4))


def generate_split_token_attack(
    order_id: str,
    seed: int = 42,
) -> tuple[list[dict[str, Any]], list[GroundTruthLabel]]:
    """Generate Attack 6 (split_token_skip) event sequence and ground-truth labels.

    Sequence:
    1. Token 1 (session 1):
       - POST /orders -> 201 (benign)
       - PUT /orders/{id}/address -> 200 (benign)
    2. Token 2 (session 2, rotated token_id & session_id for same user sub):
       - POST /orders/{id}/fulfill -> 200 (violation: SKIPPED / R2)

    Args:
        order_id: Target Order resource identifier.
        seed: Deterministic seed for event IDs.

    Returns:
        Tuple of (events, labels) where events conform to contracts/event.schema.json
        and labels are GroundTruthLabel instances.
    """
    if not isinstance(order_id, str) or not order_id.strip():
        raise ValueError("order_id must be a non-empty string.")

    clean_order_id = order_id.strip()
    rng = random.Random(f"split_token_skip:{clean_order_id}:{seed}")

    user_sub = f"user-split-{clean_order_id}"
    user_role = "admin"
    session_1 = f"sess-{user_sub}-1"
    token_1 = f"tok-{user_sub}-1"
    session_2 = f"sess-{user_sub}-2"
    token_2 = f"tok-{user_sub}-2"

    event_1_id = _seeded_uuid4(rng)
    event_2_id = _seeded_uuid4(rng)
    event_3_id = _seeded_uuid4(rng)

    events: list[dict[str, Any]] = [
        {
            "event_id": event_1_id,
            "ts_received": "2026-10-29T16:00:00.000Z",
            "ts_completed": "2026-10-29T16:00:00.050Z",
            "subject": {
                "issuer": "straph-eval",
                "sub": user_sub,
                "role": user_role,
            },
            "session_id": session_1,
            "token_id": token_1,
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
            "ts_received": "2026-10-29T16:01:00.000Z",
            "ts_completed": "2026-10-29T16:01:00.050Z",
            "subject": {
                "issuer": "straph-eval",
                "sub": user_sub,
                "role": user_role,
            },
            "session_id": session_1,
            "token_id": token_1,
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
            "ts_received": "2026-10-29T16:02:00.000Z",
            "ts_completed": "2026-10-29T16:02:00.050Z",
            "subject": {
                "issuer": "straph-eval",
                "sub": user_sub,
                "role": user_role,
            },
            "session_id": session_2,
            "token_id": token_2,
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
