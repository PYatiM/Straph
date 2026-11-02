"""API tests for the target app address and payment routes."""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest

from services.evaluation.target_app.app import create_app
from services.evaluation.target_app.repository import OrderRepository, OrderState

ALICE = {"X-User-Sub": "alice"}
BOB = {"X-User-Sub": "bob"}
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


def _state(client: httpx.AsyncClient, order_id: str) -> OrderState:
    repo: OrderRepository = client.app.state.repository  # type: ignore[attr-defined]
    return repo.get_order(order_id).state


async def _new_order(client: httpx.AsyncClient, headers: dict[str, str] = ALICE) -> str:
    await client.post("/cart/items", json={"sku": "SKU-1"}, headers=headers)
    resp = await client.post("/orders", headers=headers)
    assert resp.status_code == 201
    order_id: str = resp.json()["order_id"]
    return order_id


# --- PUT /orders/{id}/address -------------------------------------------------------------


@pytest.mark.anyio
async def test_address_from_created_moves_to_address_set(client: httpx.AsyncClient) -> None:
    order_id = await _new_order(client)
    resp = await client.put(f"/orders/{order_id}/address", json=ADDRESS, headers=ALICE)
    assert resp.status_code == 200
    assert resp.json() == {
        "order_id": order_id,
        "state": "ADDRESS_SET",
        "address": ADDRESS["address"],
    }
    assert _state(client, order_id) is OrderState.ADDRESS_SET


@pytest.mark.anyio
async def test_address_can_be_edited_again(client: httpx.AsyncClient) -> None:
    order_id = await _new_order(client)
    await client.put(f"/orders/{order_id}/address", json=ADDRESS, headers=ALICE)
    resp = await client.put(
        f"/orders/{order_id}/address", json={"address": "2 New Rd"}, headers=ALICE
    )
    assert resp.status_code == 200
    assert resp.json()["state"] == "ADDRESS_SET"
    repo: OrderRepository = client.app.state.repository  # type: ignore[attr-defined]
    assert repo.get_order(order_id).address == "2 New Rd"


@pytest.mark.anyio
async def test_address_rejected_after_payment_started(client: httpx.AsyncClient) -> None:
    order_id = await _new_order(client)
    await client.put(f"/orders/{order_id}/address", json=ADDRESS, headers=ALICE)
    await client.post(f"/orders/{order_id}/payment", headers=ALICE)
    resp = await client.put(f"/orders/{order_id}/address", json=ADDRESS, headers=ALICE)
    assert resp.status_code == 409
    assert resp.json()["detail"] == {
        "error": "invalid_state",
        "current_state": "PAYMENT_PENDING",
        "requested_state": "ADDRESS_SET",
    }
    assert _state(client, order_id) is OrderState.PAYMENT_PENDING


@pytest.mark.anyio
async def test_address_by_non_owner_is_forbidden(client: httpx.AsyncClient) -> None:
    order_id = await _new_order(client)
    resp = await client.put(f"/orders/{order_id}/address", json=ADDRESS, headers=BOB)
    assert resp.status_code == 403
    assert _state(client, order_id) is OrderState.ORDER_CREATED


@pytest.mark.anyio
async def test_address_unknown_order_is_404(client: httpx.AsyncClient) -> None:
    resp = await client.put("/orders/9999/address", json=ADDRESS, headers=ALICE)
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_address_requires_auth_header(client: httpx.AsyncClient) -> None:
    order_id = await _new_order(client)
    resp = await client.put(f"/orders/{order_id}/address", json=ADDRESS)
    assert resp.status_code == 401


@pytest.mark.anyio
async def test_address_rejects_empty_body(client: httpx.AsyncClient) -> None:
    order_id = await _new_order(client)
    resp = await client.put(f"/orders/{order_id}/address", json={"address": ""}, headers=ALICE)
    assert resp.status_code == 422


# --- POST /orders/{id}/payment ------------------------------------------------------------


@pytest.mark.anyio
async def test_payment_from_address_set_moves_to_payment_pending(
    client: httpx.AsyncClient,
) -> None:
    order_id = await _new_order(client)
    await client.put(f"/orders/{order_id}/address", json=ADDRESS, headers=ALICE)
    resp = await client.post(f"/orders/{order_id}/payment", headers=ALICE)
    assert resp.status_code == 200
    body = resp.json()
    assert body["order_id"] == order_id
    assert body["state"] == "PAYMENT_PENDING"
    assert body["payment_id"]
    assert _state(client, order_id) is OrderState.PAYMENT_PENDING
    repo: OrderRepository = client.app.state.repository  # type: ignore[attr-defined]
    assert repo.get_order(order_id).payment_id == body["payment_id"]


@pytest.mark.anyio
async def test_payment_without_address_is_rejected(client: httpx.AsyncClient) -> None:
    order_id = await _new_order(client)
    resp = await client.post(f"/orders/{order_id}/payment", headers=ALICE)
    assert resp.status_code == 409
    assert resp.json()["detail"] == {
        "error": "invalid_state",
        "current_state": "ORDER_CREATED",
        "requested_state": "PAYMENT_PENDING",
    }
    assert _state(client, order_id) is OrderState.ORDER_CREATED


@pytest.mark.anyio
async def test_payment_twice_is_rejected(client: httpx.AsyncClient) -> None:
    order_id = await _new_order(client)
    await client.put(f"/orders/{order_id}/address", json=ADDRESS, headers=ALICE)
    await client.post(f"/orders/{order_id}/payment", headers=ALICE)
    resp = await client.post(f"/orders/{order_id}/payment", headers=ALICE)
    assert resp.status_code == 409
    assert resp.json()["detail"]["current_state"] == "PAYMENT_PENDING"


@pytest.mark.anyio
async def test_payment_by_non_owner_is_forbidden(client: httpx.AsyncClient) -> None:
    order_id = await _new_order(client)
    await client.put(f"/orders/{order_id}/address", json=ADDRESS, headers=ALICE)
    resp = await client.post(f"/orders/{order_id}/payment", headers=BOB)
    assert resp.status_code == 403
    assert _state(client, order_id) is OrderState.ADDRESS_SET


@pytest.mark.anyio
async def test_payment_unknown_order_is_404(client: httpx.AsyncClient) -> None:
    resp = await client.post("/orders/9999/payment", headers=ALICE)
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_payment_requires_auth_header(client: httpx.AsyncClient) -> None:
    order_id = await _new_order(client)
    resp = await client.post(f"/orders/{order_id}/payment")
    assert resp.status_code == 401


# --- history ------------------------------------------------------------------------------


@pytest.mark.anyio
async def test_history_records_each_legal_step(client: httpx.AsyncClient) -> None:
    order_id = await _new_order(client)
    await client.put(f"/orders/{order_id}/address", json=ADDRESS, headers=ALICE)
    await client.post(f"/orders/{order_id}/payment", headers=ALICE)
    repo: OrderRepository = client.app.state.repository  # type: ignore[attr-defined]
    states = [h.to_state for h in repo.get_order(order_id).history]
    assert states == [
        OrderState.ORDER_CREATED,
        OrderState.ADDRESS_SET,
        OrderState.PAYMENT_PENDING,
    ]
