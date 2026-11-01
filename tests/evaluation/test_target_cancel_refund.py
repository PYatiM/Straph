"""API tests for the target app cancel and refund routes (terminal states)."""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest

from services.evaluation.target_app.app import create_app
from services.evaluation.target_app.repository import (
    ALLOWED_TRANSITIONS,
    OrderRecord,
    OrderRepository,
    OrderState,
)

ALICE = {"X-User-Sub": "alice"}
BOB = {"X-User-Sub": "bob"}
GATEWAY = {"X-User-Sub": "pay-svc", "X-User-Roles": "payment-gateway"}
WAREHOUSE = {"X-User-Sub": "wh-1", "X-User-Roles": "warehouse"}
CARRIER = {"X-User-Sub": "carrier-1", "X-User-Roles": "carrier"}
SUPPORT = {"X-User-Sub": "agent-1", "X-User-Roles": "support"}
ADDRESS = {"address": "1 Main St, Bengaluru"}

S = OrderState
CANCELLABLE = [S.ORDER_CREATED, S.ADDRESS_SET, S.PAYMENT_PENDING, S.PAID]
NOT_CANCELLABLE = [S.FULFILLMENT_QUEUED, S.SHIPPED, S.DELIVERED]
REFUNDABLE = [S.PAID, S.DELIVERED]
NOT_REFUNDABLE = [
    S.ORDER_CREATED,
    S.ADDRESS_SET,
    S.PAYMENT_PENDING,
    S.FULFILLMENT_QUEUED,
    S.SHIPPED,
]


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        c.app = app  # type: ignore[attr-defined]
        yield c


def _repo(client: httpx.AsyncClient) -> OrderRepository:
    repo: OrderRepository = client.app.state.repository  # type: ignore[attr-defined]
    return repo


def _order(client: httpx.AsyncClient, order_id: str) -> OrderRecord:
    return _repo(client).get_order(order_id)


async def _order_in(client: httpx.AsyncClient, target: OrderState) -> str:
    """Create an order for alice and walk it, through the API, to ``target``."""
    await client.post("/cart/items", json={"sku": "SKU-1"}, headers=ALICE)
    order_id: str = (await client.post("/orders", headers=ALICE)).json()["order_id"]
    steps = [
        (S.ADDRESS_SET, "PUT", f"/orders/{order_id}/address", ALICE, ADDRESS),
        (S.PAYMENT_PENDING, "POST", f"/orders/{order_id}/payment", ALICE, None),
        (S.PAID, "POST", "/payments/webhook", GATEWAY, {"order_id": order_id}),
        (S.FULFILLMENT_QUEUED, "POST", f"/orders/{order_id}/fulfill", WAREHOUSE, None),
        (S.SHIPPED, "POST", f"/orders/{order_id}/ship", WAREHOUSE, None),
        (S.DELIVERED, "POST", f"/orders/{order_id}/deliver", CARRIER, None),
    ]
    for state, method, url, headers, body in steps:
        if _order(client, order_id).state is target:
            break
        resp = await client.request(method, url, headers=headers, json=body)
        assert resp.status_code == 200, (state, resp.text)
    assert _order(client, order_id).state is target
    return order_id


# --- POST /orders/{id}/cancel -------------------------------------------------------------


@pytest.mark.anyio
@pytest.mark.parametrize("start", CANCELLABLE)
async def test_cancel_from_legal_state_reaches_cancelled(
    client: httpx.AsyncClient, start: OrderState
) -> None:
    order_id = await _order_in(client, start)
    resp = await client.post(f"/orders/{order_id}/cancel", headers=ALICE)
    assert resp.status_code == 200
    assert resp.json() == {"order_id": order_id, "state": "CANCELLED"}
    order = _order(client, order_id)
    assert order.state is OrderState.CANCELLED
    assert order.refunded is False
    assert order.history[-1].from_state is start


@pytest.mark.anyio
@pytest.mark.parametrize("start", NOT_CANCELLABLE)
async def test_cancel_from_illegal_state_is_rejected(
    client: httpx.AsyncClient, start: OrderState
) -> None:
    order_id = await _order_in(client, start)
    resp = await client.post(f"/orders/{order_id}/cancel", headers=ALICE)
    assert resp.status_code == 409
    assert resp.json()["detail"] == {
        "error": "invalid_state",
        "current_state": start.value,
        "requested_state": "CANCELLED",
    }
    assert _order(client, order_id).state is start


@pytest.mark.anyio
async def test_cancel_by_non_owner_is_forbidden(client: httpx.AsyncClient) -> None:
    order_id = await _order_in(client, OrderState.ORDER_CREATED)
    resp = await client.post(f"/orders/{order_id}/cancel", headers=BOB)
    assert resp.status_code == 403
    assert _order(client, order_id).state is OrderState.ORDER_CREATED


@pytest.mark.anyio
async def test_cancel_by_support_role_is_not_enough(client: httpx.AsyncClient) -> None:
    order_id = await _order_in(client, OrderState.ORDER_CREATED)
    resp = await client.post(f"/orders/{order_id}/cancel", headers=SUPPORT)
    assert resp.status_code == 403


@pytest.mark.anyio
async def test_cancel_unknown_order_is_404(client: httpx.AsyncClient) -> None:
    resp = await client.post("/orders/9999/cancel", headers=ALICE)
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_cancel_requires_sub_header(client: httpx.AsyncClient) -> None:
    resp = await client.post("/orders/1001/cancel")
    assert resp.status_code == 401


@pytest.mark.anyio
async def test_cancel_twice_is_rejected(client: httpx.AsyncClient) -> None:
    order_id = await _order_in(client, OrderState.ORDER_CREATED)
    await client.post(f"/orders/{order_id}/cancel", headers=ALICE)
    resp = await client.post(f"/orders/{order_id}/cancel", headers=ALICE)
    assert resp.status_code == 409
    assert resp.json()["detail"]["current_state"] == "CANCELLED"


# --- POST /orders/{id}/refund -------------------------------------------------------------


@pytest.mark.anyio
@pytest.mark.parametrize("start", REFUNDABLE)
async def test_refund_from_legal_state_reaches_refunded(
    client: httpx.AsyncClient, start: OrderState
) -> None:
    order_id = await _order_in(client, start)
    resp = await client.post(f"/orders/{order_id}/refund", headers=SUPPORT)
    assert resp.status_code == 200
    assert resp.json() == {"order_id": order_id, "state": "REFUNDED", "refunded": True}
    order = _order(client, order_id)
    assert order.state is OrderState.REFUNDED
    assert order.refunded is True
    assert order.history[-1].from_state is start
    assert order.history[-1].actor_sub == "agent-1"


@pytest.mark.anyio
@pytest.mark.parametrize("start", NOT_REFUNDABLE)
async def test_refund_from_illegal_state_is_rejected(
    client: httpx.AsyncClient, start: OrderState
) -> None:
    order_id = await _order_in(client, start)
    resp = await client.post(f"/orders/{order_id}/refund", headers=SUPPORT)
    assert resp.status_code == 409
    assert resp.json()["detail"] == {
        "error": "invalid_state",
        "current_state": start.value,
        "requested_state": "REFUNDED",
    }
    order = _order(client, order_id)
    assert order.state is start
    assert order.refunded is False


@pytest.mark.anyio
@pytest.mark.parametrize("headers", [ALICE, GATEWAY, WAREHOUSE, CARRIER])
async def test_refund_without_support_role_is_forbidden(
    client: httpx.AsyncClient, headers: dict[str, str]
) -> None:
    order_id = await _order_in(client, OrderState.PAID)
    resp = await client.post(f"/orders/{order_id}/refund", headers=headers)
    assert resp.status_code == 403
    assert _order(client, order_id).state is OrderState.PAID
    assert _order(client, order_id).refunded is False


@pytest.mark.anyio
async def test_refund_requires_sub_header(client: httpx.AsyncClient) -> None:
    resp = await client.post("/orders/1001/refund", headers={"X-User-Roles": "support"})
    assert resp.status_code == 401


@pytest.mark.anyio
async def test_refund_unknown_order_is_404(client: httpx.AsyncClient) -> None:
    resp = await client.post("/orders/9999/refund", headers=SUPPORT)
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_refund_twice_is_rejected(client: httpx.AsyncClient) -> None:
    order_id = await _order_in(client, OrderState.DELIVERED)
    await client.post(f"/orders/{order_id}/refund", headers=SUPPORT)
    resp = await client.post(f"/orders/{order_id}/refund", headers=SUPPORT)
    assert resp.status_code == 409
    assert resp.json()["detail"]["current_state"] == "REFUNDED"


# --- terminal states ----------------------------------------------------------------------


@pytest.mark.anyio
async def test_cancelled_order_cannot_be_refunded(client: httpx.AsyncClient) -> None:
    order_id = await _order_in(client, OrderState.PAID)
    await client.post(f"/orders/{order_id}/cancel", headers=ALICE)
    resp = await client.post(f"/orders/{order_id}/refund", headers=SUPPORT)
    assert resp.status_code == 409
    assert _order(client, order_id).state is OrderState.CANCELLED


@pytest.mark.anyio
async def test_refunded_order_cannot_be_cancelled(client: httpx.AsyncClient) -> None:
    order_id = await _order_in(client, OrderState.PAID)
    await client.post(f"/orders/{order_id}/refund", headers=SUPPORT)
    resp = await client.post(f"/orders/{order_id}/cancel", headers=ALICE)
    assert resp.status_code == 409
    assert _order(client, order_id).state is OrderState.REFUNDED


@pytest.mark.anyio
async def test_no_route_moves_a_cancelled_order_forward(client: httpx.AsyncClient) -> None:
    order_id = await _order_in(client, OrderState.PAID)
    await client.post(f"/orders/{order_id}/cancel", headers=ALICE)
    attempts = [
        client.put(f"/orders/{order_id}/address", json=ADDRESS, headers=ALICE),
        client.post(f"/orders/{order_id}/payment", headers=ALICE),
        client.post("/payments/webhook", json={"order_id": order_id}, headers=GATEWAY),
        client.post(f"/orders/{order_id}/fulfill", headers=WAREHOUSE),
        client.post(f"/orders/{order_id}/ship", headers=WAREHOUSE),
        client.post(f"/orders/{order_id}/deliver", headers=CARRIER),
    ]
    for attempt in attempts:
        assert (await attempt).status_code == 409
    assert _order(client, order_id).state is OrderState.CANCELLED


def test_terminal_states_have_no_exits() -> None:
    assert ALLOWED_TRANSITIONS[OrderState.CANCELLED] == frozenset()
    assert ALLOWED_TRANSITIONS[OrderState.REFUNDED] == frozenset()
