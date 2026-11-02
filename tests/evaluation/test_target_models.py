"""Unit tests for target app constants and Pydantic models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from services.evaluation.target_app.constants import OrderState
from services.evaluation.target_app.models import (
    AddressRequest,
    CartItem,
    CreateOrderRequest,
    PaymentRequest,
    WebhookPayload,
)


def test_order_state_enum_values() -> None:
    expected = [
        "CART_ACTIVE",
        "ORDER_CREATED",
        "ADDRESS_SET",
        "PAYMENT_PENDING",
        "PAID",
        "FULFILLMENT_QUEUED",
        "SHIPPED",
        "DELIVERED",
        "CANCELLED",
        "REFUNDED",
    ]
    actual = [s.value for s in OrderState]
    assert actual == expected
    assert len(OrderState) == 10


def test_cart_item_validations() -> None:
    item = CartItem(sku="SKU-100", quantity=3)
    assert item.sku == "SKU-100"
    assert item.quantity == 3

    # Default quantity is 1
    default_item = CartItem(sku="SKU-200")
    assert default_item.quantity == 1

    with pytest.raises(ValidationError):
        CartItem(sku="", quantity=1)

    with pytest.raises(ValidationError):
        CartItem(sku="SKU-100", quantity=0)


def test_create_order_request_validations() -> None:
    req = CreateOrderRequest(items=[CartItem(sku="SKU-1", quantity=1)])
    assert len(req.items) == 1

    empty_req = CreateOrderRequest()
    assert empty_req.items == []


def test_address_request_validations() -> None:
    req = AddressRequest(address="221B Baker St")
    assert req.address == "221B Baker St"

    with pytest.raises(ValidationError):
        AddressRequest(address="")


def test_payment_request_validations() -> None:
    req = PaymentRequest()
    assert req.payment_method == "card"

    custom_req = PaymentRequest(payment_method="upi")
    assert custom_req.payment_method == "upi"

    with pytest.raises(ValidationError):
        PaymentRequest(payment_method="")


def test_webhook_payload_validations() -> None:
    payload = WebhookPayload(order_id="order-1001")
    assert payload.order_id == "order-1001"

    with pytest.raises(ValidationError):
        WebhookPayload(order_id="")
