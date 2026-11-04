"""Attack Generator 2: Post-Shipping Address Edit (address_after_shipping).

Generates schema-valid API events and GroundTruthLabel records for an order
that advances legally through 7 steps to SHIPPED, followed by an out-of-order
PUT /orders/{id}/address call on event #8.
"""

from __future__ import annotations

import random
import uuid
from typing import Any

from services.evaluation.labels import ExpectedLabel, ExpectedRule, GroundTruthLabel

SCENARIO_ID = "ATTACK_2_ADDRESS_AFTER_SHIPPING"


def _seeded_uuid4(rng: random.Random) -> str:
    """Generate a deterministic RFC-4122 UUID4 string from a seeded RNG."""
    return str(uuid.UUID(int=rng.getrandbits(128), version=4))


def generate_post_ship_edit_attack(
    order_id: str,
    seed: int = 42,
) -> tuple[list[dict[str, Any]], list[GroundTruthLabel]]:
    """Generate Attack 2 (address_after_shipping) event sequence and ground-truth labels.

    Sequence:
    1. POST /cart/items (customer) -> 200 (benign)
    2. POST /orders (customer) -> 201 (benign)
    3. PUT /orders/{id}/address (customer) -> 200 (benign)
    4. POST /orders/{id}/payment (customer) -> 200 (benign)
    5. POST /payments/webhook (payment-gateway) -> 200 (benign)
    6. POST /orders/{id}/fulfill (warehouse) -> 200 (benign)
    7. POST /orders/{id}/ship (warehouse) -> 200 (benign, reaches SHIPPED)
    8. PUT /orders/{id}/address (customer) -> 200 (violation: OUT_OF_ORDER / R3)

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
    rng = random.Random(f"address_after_shipping:{clean_order_id}:{seed}")

    customer_sub = f"customer-{clean_order_id}"
    payment_sub = "payment-gateway"
    warehouse_sub = "warehouse-worker-1"

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
        (customer_sub, "customer", "PUT", "/orders/{id}/address", 200, "Order", clean_order_id),
    ]

    events: list[dict[str, Any]] = []
    labels: list[GroundTruthLabel] = []

    for idx, (sub, role, method, path_template, status, res_type, res_id) in enumerate(step_specs):
        event_id = _seeded_uuid4(rng)
        event: dict[str, Any] = {
            "event_id": event_id,
            "ts_received": f"2026-10-29T12:{idx:02d}:00.000Z",
            "ts_completed": f"2026-10-29T12:{idx:02d}:00.050Z",
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

        is_final_attack = idx == 7
        label = GroundTruthLabel(
            event_id=event_id,
            is_violation=is_final_attack,
            expected_label=(
                ExpectedLabel.OUT_OF_ORDER if is_final_attack else ExpectedLabel.BENIGN
            ),
            expected_rule=ExpectedRule.R3 if is_final_attack else None,
            missing_ops=[],
            scenario_id=SCENARIO_ID,
            seed=seed,
        )
        labels.append(label)

    return events, labels
