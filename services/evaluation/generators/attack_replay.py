"""Attack Generator 3: Double Refund Replay (double_refund).

Generates schema-valid API events and GroundTruthLabel records for an order
that advances to DELIVERED, executes one legal support refund (moving to
REFUNDED), and then replays a second support refund (R1 REPLAYED).
"""

from __future__ import annotations

import random
import uuid
from typing import Any

from services.evaluation.labels import ExpectedLabel, ExpectedRule, GroundTruthLabel

SCENARIO_ID = "ATTACK_3_DOUBLE_REFUND"


def _seeded_uuid4(rng: random.Random) -> str:
    """Generate a deterministic RFC-4122 UUID4 string from a seeded RNG."""
    return str(uuid.UUID(int=rng.getrandbits(128), version=4))


def generate_double_refund_attack(
    order_id: str,
    seed: int = 42,
) -> tuple[list[dict[str, Any]], list[GroundTruthLabel]]:
    """Generate Attack 3 (double_refund) event sequence and ground-truth labels.

    Sequence:
    1. Calls 1-8 advance order to DELIVERED (benign):
       - POST /cart/items (customer) -> 200
       - POST /orders (customer) -> 201
       - PUT /orders/{id}/address (customer) -> 200
       - POST /orders/{id}/payment (customer) -> 200
       - POST /payments/webhook (payment-gateway) -> 200
       - POST /orders/{id}/fulfill (warehouse) -> 200
       - POST /orders/{id}/ship (warehouse) -> 200
       - POST /orders/{id}/deliver (warehouse) -> 200
    2. Call 9: POST /orders/{id}/refund (role: 'support') -> 200 (benign, moves to REFUNDED)
    3. Call 10: POST /orders/{id}/refund (role: 'support') -> 200 (violation: REPLAYED / R1)

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
    rng = random.Random(f"double_refund:{clean_order_id}:{seed}")

    customer_sub = f"customer-{clean_order_id}"
    payment_sub = "payment-gateway"
    warehouse_sub = "warehouse-worker-1"
    support_sub = "support-agent-1"

    step_specs: list[tuple[str, str, str, str, int, str, str]] = [
        (customer_sub, "customer", "POST", "/cart/items", 200, "Cart", f"cart-{clean_order_id}"),
        (customer_sub, "customer", "POST", "/orders", 201, "Order", clean_order_id),
        (customer_sub, "customer", "PUT", "/orders/{id}/address", 200, "Order", clean_order_id),
        (customer_sub, "customer", "POST", "/orders/{id}/payment", 200, "Order", clean_order_id),
        (
            payment_sub,
            "payment-gateway",
            "POST",
            "/payments/webhook",
            200,
            "Order",
            clean_order_id,
        ),
        (warehouse_sub, "warehouse", "POST", "/orders/{id}/fulfill", 200, "Order", clean_order_id),
        (warehouse_sub, "warehouse", "POST", "/orders/{id}/ship", 200, "Order", clean_order_id),
        (warehouse_sub, "warehouse", "POST", "/orders/{id}/deliver", 200, "Order", clean_order_id),
        (support_sub, "support", "POST", "/orders/{id}/refund", 200, "Order", clean_order_id),
        (support_sub, "support", "POST", "/orders/{id}/refund", 200, "Order", clean_order_id),
    ]

    events: list[dict[str, Any]] = []
    labels: list[GroundTruthLabel] = []

    for idx, (sub, role, method, path_template, status, res_type, res_id) in enumerate(step_specs):
        event_id = _seeded_uuid4(rng)
        event: dict[str, Any] = {
            "event_id": event_id,
            "ts_received": f"2026-10-29T13:{idx:02d}:00.000Z",
            "ts_completed": f"2026-10-29T13:{idx:02d}:00.050Z",
            "subject": {
                "issuer": "straph-eval",
                "sub": sub,
                "role": role,
            },
            "session_id": f"sess-{sub}",
            "token_id": f"tok-{sub}",
            "operation": {
                "method": method,
                "path_template": path_template,
            },
            "resource_type": res_type,
            "resource_id": res_id,
            "status_code": status,
        }
        events.append(event)

        is_replay_attack = idx == len(step_specs) - 1
        label = GroundTruthLabel(
            event_id=event_id,
            is_violation=is_replay_attack,
            expected_label=ExpectedLabel.REPLAYED if is_replay_attack else ExpectedLabel.BENIGN,
            expected_rule=ExpectedRule.R1 if is_replay_attack else None,
            missing_ops=[],
            scenario_id=SCENARIO_ID,
            seed=seed,
        )
        labels.append(label)

    return events, labels
