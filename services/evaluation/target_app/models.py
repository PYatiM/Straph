"""Pydantic request and response models for the target evaluation application."""

from __future__ import annotations

from pydantic import BaseModel, Field


class CartItem(BaseModel):
    sku: str = Field(min_length=1)
    quantity: int = Field(default=1, ge=1)


class CreateOrderRequest(BaseModel):
    items: list[CartItem] = Field(default_factory=list)


class AddressRequest(BaseModel):
    address: str = Field(min_length=1)


class PaymentRequest(BaseModel):
    payment_method: str = Field(default="card", min_length=1)


class WebhookPayload(BaseModel):
    order_id: str = Field(min_length=1)
