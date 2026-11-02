"""Constants and enums for the target evaluation application."""

from __future__ import annotations

from enum import StrEnum


class OrderState(StrEnum):
    """Lifecycle states of an order."""

    CART_ACTIVE = "CART_ACTIVE"
    ORDER_CREATED = "ORDER_CREATED"
    ADDRESS_SET = "ADDRESS_SET"
    PAYMENT_PENDING = "PAYMENT_PENDING"
    PAID = "PAID"
    FULFILLMENT_QUEUED = "FULFILLMENT_QUEUED"
    SHIPPED = "SHIPPED"
    DELIVERED = "DELIVERED"
    CANCELLED = "CANCELLED"
    REFUNDED = "REFUNDED"
