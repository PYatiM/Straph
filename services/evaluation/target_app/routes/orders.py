"""Cart and order routes for the Straph evaluation target app."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, Field

from services.evaluation.target_app.repository import (
    CartStore,
    CartSummary,
    OrderItem,
    OrderRepository,
)

router = APIRouter()


class AddCartItemRequest(BaseModel):
    sku: str = Field(min_length=1)
    quantity: int = Field(default=1, ge=1)


class CartItemOut(BaseModel):
    sku: str
    quantity: int


class CartSummaryResponse(BaseModel):
    owner_sub: str
    items: list[CartItemOut]
    total_quantity: int


class CreateOrderResponse(BaseModel):
    order_id: str
    state: str


def get_caller_sub(
    x_user_sub: Annotated[str | None, Header()] = None,
) -> str:
    """Identify the caller from the ``X-User-Sub`` header (set by the gateway)."""
    if x_user_sub is None or not x_user_sub.strip():
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="missing X-User-Sub header")
    return x_user_sub.strip()


def get_repository(request: Request) -> OrderRepository:
    repository: OrderRepository = request.app.state.repository
    return repository


def get_cart_store(request: Request) -> CartStore:
    carts: CartStore = request.app.state.carts
    return carts


CallerSub = Annotated[str, Depends(get_caller_sub)]
Repository = Annotated[OrderRepository, Depends(get_repository)]
Carts = Annotated[CartStore, Depends(get_cart_store)]


def _cart_response(cart: CartSummary) -> CartSummaryResponse:
    return CartSummaryResponse(
        owner_sub=cart.owner_sub,
        items=[CartItemOut(sku=i.sku, quantity=i.quantity) for i in cart.items],
        total_quantity=cart.total_quantity,
    )


@router.post("/cart/items", response_model=CartSummaryResponse)
def add_cart_item(body: AddCartItemRequest, sub: CallerSub, carts: Carts) -> CartSummaryResponse:
    cart = carts.add_item(sub, OrderItem(sku=body.sku, quantity=body.quantity))
    return _cart_response(cart)


@router.post(
    "/orders",
    response_model=CreateOrderResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_order(sub: CallerSub, repository: Repository, carts: Carts) -> CreateOrderResponse:
    cart = carts.get_cart(sub)
    if not cart.items:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="cart is empty")
    order = repository.create_order(owner_sub=sub, items=cart.items)
    carts.clear(sub)
    return CreateOrderResponse(order_id=order.order_id, state=order.state.value)
