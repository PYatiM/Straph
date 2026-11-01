"""Cart and order routes for the Straph evaluation target app."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, Field

from services.evaluation.target_app.repository import (
    CartStore,
    CartSummary,
    InvalidStateTransitionException,
    OrderItem,
    OrderNotFoundException,
    OrderRecord,
    OrderRepository,
    OrderState,
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


class SetAddressRequest(BaseModel):
    address: str = Field(min_length=1)


class AddressResponse(BaseModel):
    order_id: str
    state: str
    address: str


class PaymentResponse(BaseModel):
    order_id: str
    state: str
    payment_id: str


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


def _load_owned_order(repository: OrderRepository, order_id: str, sub: str) -> OrderRecord:
    """Return the order if the caller owns it, else raise 404 / 403."""
    try:
        order = repository.get_order(order_id)
    except OrderNotFoundException:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="order not found") from None
    if order.owner_sub != sub:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="caller does not own this order")
    return order


def _invalid_state(exc: InvalidStateTransitionException) -> HTTPException:
    return HTTPException(
        status.HTTP_409_CONFLICT,
        detail={
            "error": "invalid_state",
            "current_state": exc.current.value,
            "requested_state": exc.requested.value,
        },
    )


@router.put("/orders/{order_id}/address", response_model=AddressResponse)
def set_address(
    order_id: str, body: SetAddressRequest, sub: CallerSub, repository: Repository
) -> AddressResponse:
    _load_owned_order(repository, order_id, sub)
    try:
        order = repository.update_order_state(
            order_id, OrderState.ADDRESS_SET, sub, address=body.address
        )
    except InvalidStateTransitionException as exc:
        raise _invalid_state(exc) from None
    return AddressResponse(order_id=order.order_id, state=order.state.value, address=body.address)


@router.post("/orders/{order_id}/payment", response_model=PaymentResponse)
def start_payment(order_id: str, sub: CallerSub, repository: Repository) -> PaymentResponse:
    _load_owned_order(repository, order_id, sub)
    payment_id = f"pay_{order_id}"
    try:
        order = repository.update_order_state(
            order_id, OrderState.PAYMENT_PENDING, sub, payment_id=payment_id
        )
    except InvalidStateTransitionException as exc:
        raise _invalid_state(exc) from None
    return PaymentResponse(order_id=order.order_id, state=order.state.value, payment_id=payment_id)
