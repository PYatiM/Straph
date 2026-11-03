"""Ground-truth reference workflow model for checkout (Straph Design Section A).

Generates contracts/examples/model_checkout.json encoding 10 states and 10 transitions
with strict conformance to contracts/model.schema.json.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from services.evaluation.target_app.constants import OrderState

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OUTPUT_PATH = REPO_ROOT / "contracts" / "examples" / "model_checkout.json"
MODEL_VERSION = "9fceb02d41@2026-10-13T09:00:00Z"
RESOURCE_TYPE = "Order"

STATES: list[dict[str, Any]] = [
    {
        "state_id": OrderState.CART_ACTIVE.value,
        "terminal": False,
        "description": "Active shopping cart accepting item additions",
    },
    {
        "state_id": OrderState.ORDER_CREATED.value,
        "terminal": False,
        "description": "Order created from cart, awaiting shipping address",
    },
    {
        "state_id": OrderState.ADDRESS_SET.value,
        "terminal": False,
        "description": "Shipping address set, awaiting payment initiation",
    },
    {
        "state_id": OrderState.PAYMENT_PENDING.value,
        "terminal": False,
        "description": "Payment authorization initiated, awaiting gateway confirmation",
    },
    {
        "state_id": OrderState.PAID.value,
        "terminal": False,
        "description": "Payment settled and confirmed by webhook",
    },
    {
        "state_id": OrderState.FULFILLMENT_QUEUED.value,
        "terminal": False,
        "description": "Paid order queued for warehouse fulfillment",
    },
    {
        "state_id": OrderState.SHIPPED.value,
        "terminal": False,
        "description": "Order dispatched and handed over to carrier",
    },
    {
        "state_id": OrderState.DELIVERED.value,
        "terminal": False,
        "description": "Order successfully delivered to customer destination",
    },
    {
        "state_id": OrderState.CANCELLED.value,
        "terminal": True,
        "description": "Order cancelled prior to fulfillment",
    },
    {
        "state_id": OrderState.REFUNDED.value,
        "terminal": True,
        "description": "Order payment refunded (single-use terminal transition)",
    },
]

TRANSITIONS: list[dict[str, Any]] = [
    {
        "transition_id": "T1",
        "operation": {"method": "POST", "path_template": "/cart/items"},
        "from_states": [OrderState.CART_ACTIVE.value],
        "to_state": OrderState.CART_ACTIVE.value,
        "allowed_roles": ["customer"],
        "single_use": False,
        "provenance": "spec",
        "support_count": 0,
        "review_status": "confirmed",
    },
    {
        "transition_id": "T2",
        "operation": {"method": "POST", "path_template": "/orders"},
        "from_states": [OrderState.CART_ACTIVE.value],
        "to_state": OrderState.ORDER_CREATED.value,
        "allowed_roles": ["customer"],
        "single_use": False,
        "provenance": "spec",
        "support_count": 0,
        "review_status": "confirmed",
    },
    {
        "transition_id": "T3",
        "operation": {"method": "PUT", "path_template": "/orders/{id}/address"},
        "from_states": [OrderState.ORDER_CREATED.value, OrderState.ADDRESS_SET.value],
        "to_state": OrderState.ADDRESS_SET.value,
        "allowed_roles": ["customer"],
        "single_use": False,
        "provenance": "spec",
        "support_count": 0,
        "review_status": "confirmed",
    },
    {
        "transition_id": "T4",
        "operation": {"method": "POST", "path_template": "/orders/{id}/payment"},
        "from_states": [OrderState.ADDRESS_SET.value],
        "to_state": OrderState.PAYMENT_PENDING.value,
        "allowed_roles": ["customer"],
        "single_use": False,
        "provenance": "spec",
        "support_count": 0,
        "review_status": "confirmed",
    },
    {
        "transition_id": "T5",
        "operation": {"method": "POST", "path_template": "/payments/webhook"},
        "from_states": [OrderState.PAYMENT_PENDING.value],
        "to_state": OrderState.PAID.value,
        "allowed_roles": ["payment-gateway"],
        "single_use": False,
        "provenance": "spec",
        "support_count": 0,
        "review_status": "confirmed",
    },
    {
        "transition_id": "T6",
        "operation": {"method": "POST", "path_template": "/orders/{id}/fulfill"},
        "from_states": [OrderState.PAID.value],
        "to_state": OrderState.FULFILLMENT_QUEUED.value,
        "allowed_roles": ["warehouse"],
        "single_use": False,
        "provenance": "spec",
        "support_count": 0,
        "review_status": "confirmed",
    },
    {
        "transition_id": "T7",
        "operation": {"method": "POST", "path_template": "/orders/{id}/ship"},
        "from_states": [OrderState.FULFILLMENT_QUEUED.value],
        "to_state": OrderState.SHIPPED.value,
        "allowed_roles": ["warehouse"],
        "single_use": False,
        "provenance": "spec",
        "support_count": 0,
        "review_status": "confirmed",
    },
    {
        "transition_id": "T8",
        "operation": {"method": "POST", "path_template": "/orders/{id}/deliver"},
        "from_states": [OrderState.SHIPPED.value],
        "to_state": OrderState.DELIVERED.value,
        "allowed_roles": ["carrier", "warehouse"],
        "single_use": False,
        "provenance": "spec",
        "support_count": 0,
        "review_status": "confirmed",
    },
    {
        "transition_id": "T9",
        "operation": {"method": "POST", "path_template": "/orders/{id}/cancel"},
        "from_states": [
            OrderState.ORDER_CREATED.value,
            OrderState.ADDRESS_SET.value,
            OrderState.PAYMENT_PENDING.value,
            OrderState.PAID.value,
        ],
        "to_state": OrderState.CANCELLED.value,
        "allowed_roles": ["customer", "support"],
        "single_use": False,
        "provenance": "spec",
        "support_count": 0,
        "review_status": "confirmed",
    },
    {
        "transition_id": "T10",
        "operation": {"method": "POST", "path_template": "/orders/{id}/refund"},
        "from_states": [OrderState.PAID.value, OrderState.DELIVERED.value],
        "to_state": OrderState.REFUNDED.value,
        "allowed_roles": ["support"],
        "single_use": True,
        "provenance": "spec",
        "support_count": 0,
        "review_status": "confirmed",
    },
]


def build_checkout_model(
    *,
    model_version: str = MODEL_VERSION,
    resource_type: str = RESOURCE_TYPE,
) -> dict[str, Any]:
    """Construct the dictionary representation of the ground-truth checkout model."""
    return {
        "model_version": model_version,
        "resource_type": resource_type,
        "initial_state": OrderState.CART_ACTIVE.value,
        "states": [dict(s) for s in STATES],
        "transitions": [
            {
                **dict(t),
                "operation": dict(t["operation"]),
                "from_states": list(t["from_states"]),
                "allowed_roles": list(t["allowed_roles"]),
            }
            for t in TRANSITIONS
        ],
    }


def generate_checkout_model(output_path: Path = DEFAULT_OUTPUT_PATH) -> Path:
    """Generate and write contracts/examples/model_checkout.json."""
    model = build_checkout_model()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(model, indent=2) + "\n"
    output_path.write_text(serialized, encoding="utf-8")
    return output_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate ground-truth checkout model JSON specification."
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_PATH,
        help="Target output path for the JSON model (default: contracts/examples/model_checkout.json)",
    )
    args = parser.parse_args()
    written_path = generate_checkout_model(args.output)
    print(f"Generated ground-truth model at: {written_path}")


if __name__ == "__main__":
    main()
