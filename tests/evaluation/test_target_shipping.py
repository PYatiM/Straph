"""API tests for the target app shipping and delivery routes."""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest

from services.evaluation.target_app.app import create_app
from services.evaluation.target_app.repository import OrderRepository, OrderState

ALICE = {"X-User-Sub": "alice"}
GATEWAY = {"X-User-Sub": "pay-svc", "X-User-Roles": "payment-gateway"}
WAREHOUSE = {"X-User-Sub": "wh-1", "X-User-Roles": "warehouse"}
CARRIER = {"X-User-Sub": "carrier-1", "X-User-Roles": "carrier"}
ADDRESS = {"address": "1 Main St, Bengaluru"}


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


def _state(client: httpx.AsyncClient, order_id: str) -> OrderState:
    return _repo(client).get_order(order_id).state


async def _order_queued(client: httpx.AsyncClient) -> str:
    """Create an order and walk it to FULFILLMENT_QUEUED."""
    await client.post("/cart/items", json={"sku": "SKU-1"}, headers=ALICE)
    order_id: str = (await client.post("/orders", headers=ALICE)).json()["order_id"]
    await client.put(f"/orders/{order_id}/address", json=ADDRESS, headers=ALICE)
    await client.post(f"/orders/{order_id}/payment", headers=ALICE)
    await client.post("/payments/webhook", json={"order_id": order_id}, headers=GATEWAY)
    await client.post(f"/orders/{order_id}/fulfill", headers=WAREHOUSE)
    assert _state(client, order_id) is OrderState.FULFILLMENT_QUEUED
    return order_id


async def _order_shipped(client: httpx.AsyncClient) -> str:
    order_id = await _order_queued(client)
    resp = await client.post(f"/orders/{order_id}/ship", headers=WAREHOUSE)
    assert resp.status_code == 200
    return order_id


# --- POST /orders/{id}/ship ---------------------------------------------------------------


@pytest.mark.anyio
async def test_ship_moves_order_to_shipped(client: httpx.AsyncClient) -> None:
    order_id = await _order_queued(client)
    resp = await client.post(f"/orders/{order_id}/ship", headers=WAREHOUSE)
    assert resp.status_code == 200
    assert resp.json() == {"order_id": order_id, "state": "SHIPPED"}
    assert _state(client, order_id) is OrderState.SHIPPED


@pytest.mark.anyio
@pytest.mark.parametrize("headers", [ALICE, GATEWAY, CARRIER])
async def test_ship_without_warehouse_role_is_forbidden(
    client: httpx.AsyncClient, headers: dict[str, str]
) -> None:
    order_id = await _order_queued(client)
    resp = await client.post(f"/orders/{order_id}/ship", headers=headers)
    assert resp.status_code == 403
    assert _state(client, order_id) is OrderState.FULFILLMENT_QUEUED


@pytest.mark.anyio
async def test_ship_requires_sub_header(client: httpx.AsyncClient) -> None:
    resp = await client.post("/orders/1001/ship", headers={"X-User-Roles": "warehouse"})
    assert resp.status_code == 401


@pytest.mark.anyio
async def test_ship_before_fulfillment_is_rejected(client: httpx.AsyncClient) -> None:
    await client.post("/cart/items", json={"sku": "SKU-1"}, headers=ALICE)
    order_id = (await client.post("/orders", headers=ALICE)).json()["order_id"]
    await client.put(f"/orders/{order_id}/address", json=ADDRESS, headers=ALICE)
    await client.post(f"/orders/{order_id}/payment", headers=ALICE)
    await client.post("/payments/webhook", json={"order_id": order_id}, headers=GATEWAY)
    resp = await client.post(f"/orders/{order_id}/ship", headers=WAREHOUSE)
    assert resp.status_code == 409
    assert resp.json()["detail"] == {
        "error": "invalid_state",
        "current_state": "PAID",
        "requested_state": "SHIPPED",
    }
    assert _state(client, order_id) is OrderState.PAID


@pytest.mark.anyio
async def test_ship_twice_is_rejected(client: httpx.AsyncClient) -> None:
    order_id = await _order_shipped(client)
    resp = await client.post(f"/orders/{order_id}/ship", headers=WAREHOUSE)
    assert resp.status_code == 409
    assert resp.json()["detail"]["current_state"] == "SHIPPED"


@pytest.mark.anyio
async def test_ship_unknown_order_is_404(client: httpx.AsyncClient) -> None:
    resp = await client.post("/orders/9999/ship", headers=WAREHOUSE)
    assert resp.status_code == 404


# --- POST /orders/{id}/deliver ------------------------------------------------------------


@pytest.mark.anyio
async def test_deliver_moves_order_to_delivered(client: httpx.AsyncClient) -> None:
    order_id = await _order_shipped(client)
    resp = await client.post(f"/orders/{order_id}/deliver", headers=CARRIER)
    assert resp.status_code == 200
    assert resp.json() == {"order_id": order_id, "state": "DELIVERED"}
    assert _state(client, order_id) is OrderState.DELIVERED


@pytest.mark.anyio
@pytest.mark.parametrize("headers", [ALICE, GATEWAY, WAREHOUSE])
async def test_deliver_without_carrier_role_is_forbidden(
    client: httpx.AsyncClient, headers: dict[str, str]
) -> None:
    order_id = await _order_shipped(client)
    resp = await client.post(f"/orders/{order_id}/deliver", headers=headers)
    assert resp.status_code == 403
    assert _state(client, order_id) is OrderState.SHIPPED


@pytest.mark.anyio
async def test_deliver_requires_sub_header(client: httpx.AsyncClient) -> None:
    resp = await client.post("/orders/1001/deliver", headers={"X-User-Roles": "carrier"})
    assert resp.status_code == 401


@pytest.mark.anyio
async def test_deliver_before_shipping_is_rejected(client: httpx.AsyncClient) -> None:
    order_id = await _order_queued(client)
    resp = await client.post(f"/orders/{order_id}/deliver", headers=CARRIER)
    assert resp.status_code == 409
    assert resp.json()["detail"] == {
        "error": "invalid_state",
        "current_state": "FULFILLMENT_QUEUED",
        "requested_state": "DELIVERED",
    }
    assert _state(client, order_id) is OrderState.FULFILLMENT_QUEUED


@pytest.mark.anyio
async def test_deliver_twice_is_rejected(client: httpx.AsyncClient) -> None:
    order_id = await _order_shipped(client)
    await client.post(f"/orders/{order_id}/deliver", headers=CARRIER)
    resp = await client.post(f"/orders/{order_id}/deliver", headers=CARRIER)
    assert resp.status_code == 409
    assert resp.json()["detail"]["current_state"] == "DELIVERED"


@pytest.mark.anyio
async def test_deliver_unknown_order_is_404(client: httpx.AsyncClient) -> None:
    resp = await client.post("/orders/9999/deliver", headers=CARRIER)
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_ship_after_delivery_is_rejected(client: httpx.AsyncClient) -> None:
    order_id = await _order_shipped(client)
    await client.post(f"/orders/{order_id}/deliver", headers=CARRIER)
    resp = await client.post(f"/orders/{order_id}/ship", headers=WAREHOUSE)
    assert resp.status_code == 409
    assert _state(client, order_id) is OrderState.DELIVERED


# --- sequential progression ---------------------------------------------------------------


@pytest.mark.anyio
async def test_status_progresses_in_order(client: httpx.AsyncClient) -> None:
    order_id = await _order_queued(client)
    observed = [_state(client, order_id)]
    await client.post(f"/orders/{order_id}/ship", headers=WAREHOUSE)
    observed.append(_state(client, order_id))
    await client.post(f"/orders/{order_id}/deliver", headers=CARRIER)
    observed.append(_state(client, order_id))
    assert observed == [
        OrderState.FULFILLMENT_QUEUED,
        OrderState.SHIPPED,
        OrderState.DELIVERED,
    ]


@pytest.mark.anyio
async def test_full_history_with_actors(client: httpx.AsyncClient) -> None:
    order_id = await _order_shipped(client)
    await client.post(f"/orders/{order_id}/deliver", headers=CARRIER)
    history = _repo(client).get_order(order_id).history
    assert [(h.to_state, h.actor_sub) for h in history][-2:] == [
        (OrderState.SHIPPED, "wh-1"),
        (OrderState.DELIVERED, "carrier-1"),
    ]
    assert [h.to_state for h in history] == [
        OrderState.ORDER_CREATED,
        OrderState.ADDRESS_SET,
        OrderState.PAYMENT_PENDING,
        OrderState.PAID,
        OrderState.FULFILLMENT_QUEUED,
        OrderState.SHIPPED,
        OrderState.DELIVERED,
    ]
