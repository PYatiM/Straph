"""Tests for the planted business-logic flaws (flaws.py) and their route integration."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
import pytest

from services.evaluation.target_app.app import create_app
from services.evaluation.target_app.flaws import (
    ENV_BOLA_CANCEL,
    ENV_DOUBLE_REFUND,
    ENV_POST_SHIP_ADDRESS_EDIT,
    ENV_SKIP_PAYMENT,
    Flaws,
)
from services.evaluation.target_app.repository import OrderRecord, OrderRepository, OrderState

ALICE = {"X-User-Sub": "alice"}
MALLORY = {"X-User-Sub": "mallory"}
GATEWAY = {"X-User-Sub": "pay-svc", "X-User-Roles": "payment-gateway"}
WAREHOUSE = {"X-User-Sub": "wh-1", "X-User-Roles": "warehouse"}
CARRIER = {"X-User-Sub": "carrier-1", "X-User-Roles": "carrier"}
SUPPORT = {"X-User-Sub": "agent-1", "X-User-Roles": "support"}
ADDRESS = {"address": "1 Main St, Bengaluru"}
NEW_ADDRESS = {"address": "99 Attacker Ave"}

S = OrderState
ALL_ENV = (ENV_SKIP_PAYMENT, ENV_POST_SHIP_ADDRESS_EDIT, ENV_DOUBLE_REFUND, ENV_BOLA_CANCEL)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make sure flags set on the developer's machine never leak into these tests."""
    for name in ALL_ENV:
        monkeypatch.delenv(name, raising=False)


@asynccontextmanager
async def _client(flaws: Flaws | None = None) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(flaws=flaws if flaws is not None else Flaws())
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        c.app = app  # type: ignore[attr-defined]
        yield c


def _order(client: httpx.AsyncClient, order_id: str) -> OrderRecord:
    repo: OrderRepository = client.app.state.repository  # type: ignore[attr-defined]
    return repo.get_order(order_id)


async def _order_in(client: httpx.AsyncClient, target: OrderState) -> str:
    """Create an order for alice and walk it, through legal calls only, to ``target``."""
    await client.post("/cart/items", json={"sku": "SKU-1"}, headers=ALICE)
    order_id: str = (await client.post("/orders", headers=ALICE)).json()["order_id"]
    steps = [
        ("PUT", f"/orders/{order_id}/address", ALICE, ADDRESS),
        ("POST", f"/orders/{order_id}/payment", ALICE, None),
        ("POST", "/payments/webhook", GATEWAY, {"order_id": order_id}),
        ("POST", f"/orders/{order_id}/fulfill", WAREHOUSE, None),
        ("POST", f"/orders/{order_id}/ship", WAREHOUSE, None),
        ("POST", f"/orders/{order_id}/deliver", CARRIER, None),
    ]
    for method, url, headers, body in steps:
        if _order(client, order_id).state is target:
            break
        resp = await client.request(method, url, headers=headers, json=body)
        assert resp.status_code == 200, resp.text
    assert _order(client, order_id).state is target
    return order_id


# --- Flaws configuration ------------------------------------------------------------------


def test_all_flaws_are_off_by_default() -> None:
    flaws = Flaws.from_env({})
    assert flaws == Flaws()
    assert flaws.enabled == ()


@pytest.mark.parametrize("value", ["true", "TRUE", "True", "1", "yes", "on", " true "])
def test_true_values_enable_a_flag(value: str) -> None:
    assert Flaws.from_env({ENV_SKIP_PAYMENT: value}).skip_payment is True


@pytest.mark.parametrize("value", ["false", "0", "no", "off", "", "maybe", "truee"])
def test_other_values_leave_flag_off(value: str) -> None:
    assert Flaws.from_env({ENV_SKIP_PAYMENT: value}).skip_payment is False


def test_each_env_var_controls_only_its_own_flag() -> None:
    assert Flaws.from_env({ENV_SKIP_PAYMENT: "true"}) == Flaws(skip_payment=True)
    assert Flaws.from_env({ENV_POST_SHIP_ADDRESS_EDIT: "true"}) == Flaws(
        post_ship_address_edit=True
    )
    assert Flaws.from_env({ENV_DOUBLE_REFUND: "true"}) == Flaws(double_refund=True)
    assert Flaws.from_env({ENV_BOLA_CANCEL: "true"}) == Flaws(bola_cancel=True)


def test_enabled_lists_active_env_names() -> None:
    flaws = Flaws(skip_payment=True, bola_cancel=True)
    assert flaws.enabled == (ENV_SKIP_PAYMENT, ENV_BOLA_CANCEL)


@pytest.mark.anyio
async def test_create_app_reads_flags_from_process_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ENV_BOLA_CANCEL, "true")
    app = create_app()
    assert app.state.flaws == Flaws(bola_cancel=True)


# --- ALLOW_SKIP_PAYMENT -------------------------------------------------------------------


@pytest.mark.anyio
async def test_fulfill_without_payment_is_blocked_when_flag_off() -> None:
    async with _client() as client:
        order_id = await _order_in(client, S.ADDRESS_SET)
        resp = await client.post(f"/orders/{order_id}/fulfill", headers=WAREHOUSE)
        assert resp.status_code == 409
        assert _order(client, order_id).state is S.ADDRESS_SET


@pytest.mark.anyio
async def test_fulfill_without_payment_works_when_flag_on() -> None:
    async with _client(Flaws(skip_payment=True)) as client:
        order_id = await _order_in(client, S.ADDRESS_SET)
        resp = await client.post(f"/orders/{order_id}/fulfill", headers=WAREHOUSE)
        assert resp.status_code == 200
        assert resp.json() == {"order_id": order_id, "state": "FULFILLMENT_QUEUED"}
        order = _order(client, order_id)
        assert order.state is S.FULFILLMENT_QUEUED
        assert order.payment_id is None
        assert order.history[-1].from_state is S.ADDRESS_SET


@pytest.mark.anyio
@pytest.mark.parametrize("start", [S.ORDER_CREATED, S.PAYMENT_PENDING])
async def test_skip_payment_flag_does_not_open_other_states(start: OrderState) -> None:
    async with _client(Flaws(skip_payment=True)) as client:
        order_id = await _order_in(client, start)
        resp = await client.post(f"/orders/{order_id}/fulfill", headers=WAREHOUSE)
        assert resp.status_code == 409
        assert _order(client, order_id).state is start


@pytest.mark.anyio
async def test_skip_payment_still_requires_warehouse_role() -> None:
    async with _client(Flaws(skip_payment=True)) as client:
        order_id = await _order_in(client, S.ADDRESS_SET)
        resp = await client.post(f"/orders/{order_id}/fulfill", headers=ALICE)
        assert resp.status_code == 403
        assert _order(client, order_id).state is S.ADDRESS_SET


# --- ALLOW_POST_SHIP_ADDRESS_EDIT ---------------------------------------------------------


@pytest.mark.anyio
async def test_address_edit_after_shipping_is_blocked_when_flag_off() -> None:
    async with _client() as client:
        order_id = await _order_in(client, S.SHIPPED)
        resp = await client.put(f"/orders/{order_id}/address", json=NEW_ADDRESS, headers=ALICE)
        assert resp.status_code == 409
        order = _order(client, order_id)
        assert order.state is S.SHIPPED
        assert order.address == ADDRESS["address"]


@pytest.mark.anyio
async def test_address_edit_after_shipping_works_when_flag_on() -> None:
    async with _client(Flaws(post_ship_address_edit=True)) as client:
        order_id = await _order_in(client, S.SHIPPED)
        resp = await client.put(f"/orders/{order_id}/address", json=NEW_ADDRESS, headers=ALICE)
        assert resp.status_code == 200
        assert resp.json()["address"] == NEW_ADDRESS["address"]
        order = _order(client, order_id)
        assert order.address == NEW_ADDRESS["address"]
        assert order.state is S.ADDRESS_SET
        assert order.history[-1].from_state is S.SHIPPED


@pytest.mark.anyio
async def test_post_ship_flag_does_not_open_delivered_state() -> None:
    async with _client(Flaws(post_ship_address_edit=True)) as client:
        order_id = await _order_in(client, S.DELIVERED)
        resp = await client.put(f"/orders/{order_id}/address", json=NEW_ADDRESS, headers=ALICE)
        assert resp.status_code == 409
        assert _order(client, order_id).state is S.DELIVERED


@pytest.mark.anyio
async def test_post_ship_flag_still_requires_owner() -> None:
    async with _client(Flaws(post_ship_address_edit=True)) as client:
        order_id = await _order_in(client, S.SHIPPED)
        resp = await client.put(f"/orders/{order_id}/address", json=NEW_ADDRESS, headers=MALLORY)
        assert resp.status_code == 403
        assert _order(client, order_id).address == ADDRESS["address"]


# --- ALLOW_DOUBLE_REFUND ------------------------------------------------------------------


@pytest.mark.anyio
async def test_second_refund_is_blocked_when_flag_off() -> None:
    async with _client() as client:
        order_id = await _order_in(client, S.PAID)
        assert (await client.post(f"/orders/{order_id}/refund", headers=SUPPORT)).status_code == 200
        resp = await client.post(f"/orders/{order_id}/refund", headers=SUPPORT)
        assert resp.status_code == 409


@pytest.mark.anyio
async def test_second_refund_works_when_flag_on() -> None:
    async with _client(Flaws(double_refund=True)) as client:
        order_id = await _order_in(client, S.PAID)
        first = await client.post(f"/orders/{order_id}/refund", headers=SUPPORT)
        second = await client.post(f"/orders/{order_id}/refund", headers=SUPPORT)
        third = await client.post(f"/orders/{order_id}/refund", headers=SUPPORT)
        assert (first.status_code, second.status_code, third.status_code) == (200, 200, 200)
        assert second.json() == {"order_id": order_id, "state": "REFUNDED", "refunded": True}
        order = _order(client, order_id)
        assert order.state is S.REFUNDED
        refunds = [h for h in order.history if h.to_state is S.REFUNDED]
        assert len(refunds) == 3


@pytest.mark.anyio
async def test_double_refund_flag_does_not_allow_refund_from_other_states() -> None:
    async with _client(Flaws(double_refund=True)) as client:
        order_id = await _order_in(client, S.SHIPPED)
        resp = await client.post(f"/orders/{order_id}/refund", headers=SUPPORT)
        assert resp.status_code == 409
        assert _order(client, order_id).refunded is False


@pytest.mark.anyio
async def test_double_refund_still_requires_support_role() -> None:
    async with _client(Flaws(double_refund=True)) as client:
        order_id = await _order_in(client, S.PAID)
        await client.post(f"/orders/{order_id}/refund", headers=SUPPORT)
        resp = await client.post(f"/orders/{order_id}/refund", headers=ALICE)
        assert resp.status_code == 403


# --- ALLOW_BOLA_CANCEL --------------------------------------------------------------------


@pytest.mark.anyio
async def test_cancel_by_non_owner_is_blocked_when_flag_off() -> None:
    async with _client() as client:
        order_id = await _order_in(client, S.ORDER_CREATED)
        resp = await client.post(f"/orders/{order_id}/cancel", headers=MALLORY)
        assert resp.status_code == 403
        assert _order(client, order_id).state is S.ORDER_CREATED


@pytest.mark.anyio
async def test_cancel_by_non_owner_works_when_flag_on() -> None:
    async with _client(Flaws(bola_cancel=True)) as client:
        order_id = await _order_in(client, S.PAID)
        resp = await client.post(f"/orders/{order_id}/cancel", headers=MALLORY)
        assert resp.status_code == 200
        assert resp.json() == {"order_id": order_id, "state": "CANCELLED"}
        order = _order(client, order_id)
        assert order.state is S.CANCELLED
        assert order.owner_sub == "alice"
        assert order.history[-1].actor_sub == "mallory"


@pytest.mark.anyio
async def test_bola_flag_still_requires_authentication() -> None:
    async with _client(Flaws(bola_cancel=True)) as client:
        order_id = await _order_in(client, S.ORDER_CREATED)
        resp = await client.post(f"/orders/{order_id}/cancel")
        assert resp.status_code == 401
        assert _order(client, order_id).state is S.ORDER_CREATED


@pytest.mark.anyio
async def test_bola_flag_still_respects_state_rules() -> None:
    async with _client(Flaws(bola_cancel=True)) as client:
        order_id = await _order_in(client, S.SHIPPED)
        resp = await client.post(f"/orders/{order_id}/cancel", headers=MALLORY)
        assert resp.status_code == 409
        assert _order(client, order_id).state is S.SHIPPED


@pytest.mark.anyio
async def test_bola_flag_unknown_order_is_404() -> None:
    async with _client(Flaws(bola_cancel=True)) as client:
        resp = await client.post("/orders/9999/cancel", headers=MALLORY)
        assert resp.status_code == 404


# --- isolation between flags --------------------------------------------------------------


@pytest.mark.anyio
async def test_enabling_one_flaw_does_not_enable_the_others() -> None:
    async with _client(Flaws(bola_cancel=True)) as client:
        order_id = await _order_in(client, S.ADDRESS_SET)
        skip = await client.post(f"/orders/{order_id}/fulfill", headers=WAREHOUSE)
        assert skip.status_code == 409

        shipped = await _order_in(client, S.SHIPPED)
        edit = await client.put(f"/orders/{shipped}/address", json=NEW_ADDRESS, headers=ALICE)
        assert edit.status_code == 409
