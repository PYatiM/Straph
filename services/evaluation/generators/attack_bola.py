"""Attack Generator 4: BOLA Cross-User Order Cancellation (unauthorized_cancel).

Generates schema-valid API events and GroundTruthLabel records for an order
created and configured by User A ("victim-user"), then cancelled by User B
("attacker-user", role: "customer"), triggering an R0 IDENTITY_MISMATCH alert.
"""

from __future__ import annotations

import random
import uuid
from typing import Any

from services.evaluation.labels import ExpectedLabel, ExpectedRule, GroundTruthLabel

SCENARIO_ID = "ATTACK_4_BOLA_CANCEL"
VICTIM_SUB = "victim-user"
ATTACKER_SUB = "attacker-user"


def _seeded_uuid4(rng: random.Random) -> str:
    """Generate a deterministic RFC-4122 UUID4 string from a seeded RNG."""
    return str(uuid.UUID(int=rng.getrandbits(128), version=4))


def generate_bola_attack(
    order_id: str,
    seed: int = 42,
) -> tuple[list[dict[str, Any]], list[GroundTruthLabel]]:
    """Generate Attack 4 (unauthorized_cancel / BOLA) event sequence and ground-truth labels.

    Sequence:
    1. User A (sub: "victim-user", role: "customer") creates order (POST /orders -> 201)
       and sets address (PUT /orders/{id}/address -> 200) (benign).
    2. User B (sub: "attacker-user", role: "customer") calls POST /orders/{id}/cancel -> 200
       (violation: IDENTITY_MISMATCH / R0).

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
    rng = random.Random(f"bola_cancel:{clean_order_id}:{seed}")

    event_1_id = _seeded_uuid4(rng)
    event_2_id = _seeded_uuid4(rng)
    event_3_id = _seeded_uuid4(rng)

    events: list[dict[str, Any]] = [
        {
            "event_id": event_1_id,
            "ts_received": "2026-10-29T14:00:00.000Z",
            "ts_completed": "2026-10-29T14:00:00.050Z",
            "subject": {
                "issuer": "straph-eval",
                "sub": VICTIM_SUB,
                "role": "customer",
            },
            "session_id": f"sess-{VICTIM_SUB}-{clean_order_id}",
            "token_id": f"tok-{VICTIM_SUB}-{clean_order_id}",
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
            "ts_received": "2026-10-29T14:01:00.000Z",
            "ts_completed": "2026-10-29T14:01:00.050Z",
            "subject": {
                "issuer": "straph-eval",
                "sub": VICTIM_SUB,
                "role": "customer",
            },
            "session_id": f"sess-{VICTIM_SUB}-{clean_order_id}",
            "token_id": f"tok-{VICTIM_SUB}-{clean_order_id}",
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
            "ts_received": "2026-10-29T14:02:00.000Z",
            "ts_completed": "2026-10-29T14:02:00.050Z",
            "subject": {
                "issuer": "straph-eval",
                "sub": ATTACKER_SUB,
                "role": "customer",
            },
            "session_id": f"sess-{ATTACKER_SUB}-{clean_order_id}",
            "token_id": f"tok-{ATTACKER_SUB}-{clean_order_id}",
            "operation": {
                "method": "POST",
                "path_template": "/orders/{id}/cancel",
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
            expected_label=ExpectedLabel.IDENTITY_MISMATCH,
            expected_rule=ExpectedRule.R0,
            missing_ops=[],
            scenario_id=SCENARIO_ID,
            seed=seed,
        ),
    ]

    return events, labels
