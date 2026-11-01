"""API tests for the target app payment webhook and fulfillment routes."""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest

from services.evaluation.target_app.app import create_app
from services.evaluation.target_app.repository import OrderRepository, OrderState

ALICE = {"X-User-Sub": "alice"}
GATEWAY = {"X-User-Sub": "pay-svc", "X-User-Roles": "payment-gateway"}
WAREHOUSE = {"X-User-Sub": "wh-1", "X-User-Roles": "warehouse"}
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


async def _order_in_payment_pending(client: httpx.AsyncClient) -> str:
    await client.post("/cart/items", json={"sku": "SKU-1"}, headers=ALICE)
    order_id: str = (await client.post("/orders", headers=ALICE)).json()["order_id"]
    await client.put(f"/orders/{order_id}/address", json=ADDRESS, headers=ALICE)
    await client.post(f"/orders/{order_id}/payment", headers=ALICE)
    return order_id


async def _order_in_paid(client: httpx.AsyncClient) -> str:
    order_id = await _order_in_payment_pending(client)
    resp = await client.post("/payments/webhook", json={"order_id": order_id}, headers=GATEWAY)
    assert resp.status_code == 200
    return order_id


# --- POST /payments/webhook ---------------------------------------------------------------


@pytest.mark.anyio
async def test_webhook_moves_order_to_paid(client: httpx.AsyncClient) -> None:
    order_id = await _order_in_payment_pending(client)
    resp = await client.post("/payments/webhook", json={"order_id": order_id}, headers=GATEWAY)
    assert resp.status_code == 200
    assert resp.json() == {"order_id": order_id, "state": "PAID"}
    assert _state(client, order_id) is OrderState.PAID


@pytest.mark.anyio
async def test_webhook_records_gateway_in_history(client: httpx.AsyncClient) -> None:
    order_id = await _order_in_payment_pending(client)
    await client.post("/payments/webhook", json={"order_id": order_id}, headers=GATEWAY)
    last = _repo(client).get_order(order_id).history[-1]
    assert last.to_state is OrderState.PAID
    assert last.actor_sub == "pay-svc"


@pytest.mark.anyio
async def test_webhook_accepts_multiple_roles(client: httpx.AsyncClient) -> None:
    order_id = await _order_in_payment_pending(client)
    headers = {"X-User-Sub": "svc", "X-User-Roles": "audit, payment-gateway"}
    resp = await client.post("/payments/webhook", json={"order_id": order_id}, headers=headers)
    assert resp.status_code == 200


@pytest.mark.anyio
@pytest.mark.parametrize(
    "headers",
    [
        {"X-User-Sub": "alice"},
        {"X-User-Sub": "alice", "X-User-Roles": ""},
        {"X-User-Sub": "wh-1", "X-User-Roles": "warehouse"},
        {"X-User-Sub": "x", "X-User-Roles": "payment-gateway-admin"},
    ],
)
async def test_webhook_without_gateway_role_is_forbidden(
    client: httpx.AsyncClient, headers: dict[str, str]
) -> None:
    order_id = await _order_in_payment_pending(client)
    resp = await client.post("/payments/webhook", json={"order_id": order_id}, headers=headers)
    assert resp.status_code == 403
    assert _state(client, order_id) is OrderState.PAYMENT_PENDING


@pytest.mark.anyio
async def test_webhook_requires_sub_header(client: httpx.AsyncClient) -> None:
    resp = await client.post(
        "/payments/webhook",
        json={"order_id": "1001"},
        headers={"X-User-Roles": "payment-gateway"},
    )
    assert resp.status_code == 401


@pytest.mark.anyio
async def test_webhook_before_payment_started_is_rejected(client: httpx.AsyncClient) -> None:
    await client.post("/cart/items", json={"sku": "SKU-1"}, headers=ALICE)
    order_id = (await client.post("/orders", headers=ALICE)).json()["order_id"]
    resp = await client.post("/payments/webhook", json={"order_id": order_id}, headers=GATEWAY)
    assert resp.status_code == 409
    assert resp.json()["detail"] == {
        "error": "invalid_state",
        "current_state": "ORDER_CREATED",
        "requested_state": "PAID",
    }
    assert _state(client, order_id) is OrderState.ORDER_CREATED


@pytest.mark.anyio
async def test_webhook_twice_is_rejected(client: httpx.AsyncClient) -> None:
    order_id = await _order_in_paid(client)
    resp = await client.post("/payments/webhook", json={"order_id": order_id}, headers=GATEWAY)
    assert resp.status_code == 409
    assert resp.json()["detail"]["current_state"] == "PAID"


@pytest.mark.anyio
async def test_webhook_unknown_order_is_404(client: httpx.AsyncClient) -> None:
    resp = await client.post("/payments/webhook", json={"order_id": "9999"}, headers=GATEWAY)
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_webhook_rejects_bad_body(client: httpx.AsyncClient) -> None:
    resp = await client.post("/payments/webhook", json={}, headers=GATEWAY)
    assert resp.status_code == 422


# --- POST /orders/{id}/fulfill ------------------------------------------------------------


@pytest.mark.anyio
async def test_fulfill_moves_order_to_fulfillment_queued(client: httpx.AsyncClient) -> None:
    order_id = await _order_in_paid(client)
    resp = await client.post(f"/orders/{order_id}/fulfill", headers=WAREHOUSE)
    assert resp.status_code == 200
    assert resp.json() == {"order_id": order_id, "state": "FULFILLMENT_QUEUED"}
    assert _state(client, order_id) is OrderState.FULFILLMENT_QUEUED


@pytest.mark.anyio
@pytest.mark.parametrize(
    "headers",
    [
        {"X-User-Sub": "alice"},
        {"X-User-Sub": "pay-svc", "X-User-Roles": "payment-gateway"},
        {"X-User-Sub": "x", "X-User-Roles": "Warehouse"},
    ],
)
async def test_fulfill_without_warehouse_role_is_forbidden(
    client: httpx.AsyncClient, headers: dict[str, str]
) -> None:
    order_id = await _order_in_paid(client)
    resp = await client.post(f"/orders/{order_id}/fulfill", headers=headers)
    assert resp.status_code == 403
    assert _state(client, order_id) is OrderState.PAID


@pytest.mark.anyio
async def test_fulfill_requires_sub_header(client: httpx.AsyncClient) -> None:
    resp = await client.post("/orders/1001/fulfill", headers={"X-User-Roles": "warehouse"})
    assert resp.status_code == 401


@pytest.mark.anyio
async def test_fulfill_before_payment_confirmed_is_rejected(client: httpx.AsyncClient) -> None:
    order_id = await _order_in_payment_pending(client)
    resp = await client.post(f"/orders/{order_id}/fulfill", headers=WAREHOUSE)
    assert resp.status_code == 409
    assert resp.json()["detail"] == {
        "error": "invalid_state",
        "current_state": "PAYMENT_PENDING",
        "requested_state": "FULFILLMENT_QUEUED",
    }
    assert _state(client, order_id) is OrderState.PAYMENT_PENDING


@pytest.mark.anyio
async def test_fulfill_twice_is_rejected(client: httpx.AsyncClient) -> None:
    order_id = await _order_in_paid(client)
    await client.post(f"/orders/{order_id}/fulfill", headers=WAREHOUSE)
    resp = await client.post(f"/orders/{order_id}/fulfill", headers=WAREHOUSE)
    assert resp.status_code == 409
    assert resp.json()["detail"]["current_state"] == "FULFILLMENT_QUEUED"


@pytest.mark.anyio
async def test_fulfill_unknown_order_is_404(client: httpx.AsyncClient) -> None:
    resp = await client.post("/orders/9999/fulfill", headers=WAREHOUSE)
    assert resp.status_code == 404


# --- whole flow ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_full_flow_history(client: httpx.AsyncClient) -> None:
    order_id = await _order_in_paid(client)
    await client.post(f"/orders/{order_id}/fulfill", headers=WAREHOUSE)
    history = _repo(client).get_order(order_id).history
    assert [(h.to_state, h.actor_sub) for h in history] == [
        (OrderState.ORDER_CREATED, "alice"),
        (OrderState.ADDRESS_SET, "alice"),
        (OrderState.PAYMENT_PENDING, "alice"),
        (OrderState.PAID, "pay-svc"),
        (OrderState.FULFILLMENT_QUEUED, "wh-1"),
    ]
