"""API tests for the target app cart and order creation routes."""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest

from services.evaluation.target_app.app import create_app
from services.evaluation.target_app.repository import OrderRepository, OrderState

ALICE = {"X-User-Sub": "alice"}
BOB = {"X-User-Sub": "bob"}


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


async def _add(client: httpx.AsyncClient, headers: dict[str, str], sku: str, qty: int = 1) -> None:
    resp = await client.post("/cart/items", json={"sku": sku, "quantity": qty}, headers=headers)
    assert resp.status_code == 200


# --- POST /cart/items ---------------------------------------------------------------------


@pytest.mark.anyio
async def test_add_cart_item_returns_200_with_summary(client: httpx.AsyncClient) -> None:
    resp = await client.post("/cart/items", json={"sku": "SKU-1", "quantity": 2}, headers=ALICE)
    assert resp.status_code == 200
    assert resp.json() == {
        "owner_sub": "alice",
        "items": [{"sku": "SKU-1", "quantity": 2}],
        "total_quantity": 2,
    }


@pytest.mark.anyio
async def test_add_same_sku_merges_quantities(client: httpx.AsyncClient) -> None:
    await _add(client, ALICE, "SKU-1", 1)
    resp = await client.post("/cart/items", json={"sku": "SKU-1", "quantity": 3}, headers=ALICE)
    assert resp.json()["items"] == [{"sku": "SKU-1", "quantity": 4}]
    assert resp.json()["total_quantity"] == 4


@pytest.mark.anyio
async def test_carts_are_separate_per_caller(client: httpx.AsyncClient) -> None:
    await _add(client, ALICE, "SKU-1")
    resp = await client.post("/cart/items", json={"sku": "SKU-2"}, headers=BOB)
    assert resp.json()["items"] == [{"sku": "SKU-2", "quantity": 1}]


@pytest.mark.anyio
async def test_add_cart_item_requires_auth_header(client: httpx.AsyncClient) -> None:
    resp = await client.post("/cart/items", json={"sku": "SKU-1"})
    assert resp.status_code == 401


@pytest.mark.anyio
@pytest.mark.parametrize("body", [{"sku": ""}, {"sku": "SKU-1", "quantity": 0}, {}])
async def test_add_cart_item_rejects_bad_body(
    client: httpx.AsyncClient, body: dict[str, object]
) -> None:
    resp = await client.post("/cart/items", json=body, headers=ALICE)
    assert resp.status_code == 422


# --- POST /orders -------------------------------------------------------------------------


@pytest.mark.anyio
async def test_create_order_returns_201_and_numeric_id(client: httpx.AsyncClient) -> None:
    await _add(client, ALICE, "SKU-1")
    resp = await client.post("/orders", headers=ALICE)
    assert resp.status_code == 201
    assert resp.json() == {"order_id": "1001", "state": "ORDER_CREATED"}


@pytest.mark.anyio
async def test_create_order_binds_owner_from_header(client: httpx.AsyncClient) -> None:
    await _add(client, ALICE, "SKU-1", 2)
    order_id = (await client.post("/orders", headers=ALICE)).json()["order_id"]
    repo: OrderRepository = client.app.state.repository  # type: ignore[attr-defined]
    order = repo.get_order(order_id)
    assert order.owner_sub == "alice"
    assert order.state is OrderState.ORDER_CREATED
    assert order.items[0].quantity == 2


@pytest.mark.anyio
async def test_order_ids_increase(client: httpx.AsyncClient) -> None:
    await _add(client, ALICE, "SKU-1")
    first = (await client.post("/orders", headers=ALICE)).json()["order_id"]
    await _add(client, BOB, "SKU-2")
    second = (await client.post("/orders", headers=BOB)).json()["order_id"]
    assert (first, second) == ("1001", "1002")


@pytest.mark.anyio
async def test_create_order_empties_the_cart(client: httpx.AsyncClient) -> None:
    await _add(client, ALICE, "SKU-1")
    await client.post("/orders", headers=ALICE)
    again = await client.post("/orders", headers=ALICE)
    assert again.status_code == 400


@pytest.mark.anyio
async def test_create_order_with_empty_cart_is_rejected(client: httpx.AsyncClient) -> None:
    resp = await client.post("/orders", headers=ALICE)
    assert resp.status_code == 400
    assert resp.json()["detail"] == "cart is empty"


@pytest.mark.anyio
async def test_create_order_requires_auth_header(client: httpx.AsyncClient) -> None:
    resp = await client.post("/orders")
    assert resp.status_code == 401
