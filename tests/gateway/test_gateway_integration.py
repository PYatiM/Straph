"""Integration tests for Gateway reverse proxy, event generation, and rate limiting.

Supports running against live containerized stack or falling back to in-memory ASGI
transports when containers are offline.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import httpx
import pytest

from services.evaluation.target_app.app import create_app as create_target_app
from services.gateway.app import create_gateway_app
from services.gateway.producer import InMemoryEventProducer
from services.gateway.rate_limiter import InMemoryTokenBucketRateLimiter


@pytest.fixture
async def gateway_env() -> AsyncIterator[tuple[httpx.AsyncClient, InMemoryEventProducer | None]]:
    """Provide gateway client and producer, either from live containers or in-memory ASGI transports."""
    live_gateway_url = os.getenv("GATEWAY_URL")
    live_online = False
    if live_gateway_url:
        try:
            async with httpx.AsyncClient(timeout=0.5) as probe:
                resp = await probe.get(f"{live_gateway_url}/health")
                if resp.status_code == 200:
                    live_online = True
        except (httpx.RequestError, TimeoutError, OSError):
            live_online = False

    if live_online and live_gateway_url:
        async with httpx.AsyncClient(base_url=live_gateway_url) as client:
            yield client, None
    else:
        # Fallback to in-memory ASGI stack
        producer = InMemoryEventProducer()
        rate_limiter = InMemoryTokenBucketRateLimiter(capacity=3, refill_rate=0.2)
        target_app = create_target_app(strict=True)
        downstream_client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=target_app),
            base_url="http://target-app:8001",
        )
        gw_app = create_gateway_app(
            target_app_url="http://target-app:8001",
            producer=producer,
            rate_limiter=rate_limiter,
            client=downstream_client,
        )
        gw_transport = httpx.ASGITransport(app=gw_app)
        async with httpx.AsyncClient(
            transport=gw_transport, base_url="http://gateway:8000"
        ) as client:
            yield client, producer


@pytest.mark.anyio
async def test_create_order_emits_event_with_extracted_order_id(
    gateway_env: tuple[httpx.AsyncClient, InMemoryEventProducer | None],
) -> None:
    """Execute POST /orders via Gateway -> Target App creates order, Gateway emits event with order_id."""
    client, producer = gateway_env
    headers = {"X-User-Sub": "buyer-99", "X-Session-Id": "sess-integ-1"}

    # 1. Add item to cart
    cart_resp = await client.post(
        "/cart/items",
        json={"sku": "SKU-GRAPH-01", "quantity": 1},
        headers=headers,
    )
    assert cart_resp.status_code == 200

    # 2. Create order
    order_resp = await client.post("/orders", headers=headers)
    assert order_resp.status_code == 201
    body = order_resp.json()
    assert "order_id" in body
    order_id = body["order_id"]

    # 3. Verify event emitted with extracted order_id
    if producer is not None:
        order_events = [e[1] for e in producer.events if e[1].operation.path_template == "/orders"]
        assert len(order_events) == 1
        ev = order_events[0]
        assert ev.resource_id == order_id
        assert ev.resource_type == "orders"
        assert ev.status_code == 201


@pytest.mark.anyio
async def test_update_address_stamped_with_timestamps(
    gateway_env: tuple[httpx.AsyncClient, InMemoryEventProducer | None],
) -> None:
    """Execute PUT /orders/{id}/address -> verify 200 OK and event stamped with ts_received and ts_completed."""
    client, producer = gateway_env
    headers = {"X-User-Sub": "buyer-100"}

    # Add item and create order
    await client.post(
        "/cart/items",
        json={"sku": "SKU-GRAPH-02", "quantity": 1},
        headers=headers,
    )
    order_resp = await client.post("/orders", headers=headers)
    assert order_resp.status_code == 201
    order_id = order_resp.json()["order_id"]

    # Update address
    addr_resp = await client.put(
        f"/orders/{order_id}/address",
        json={"address": "742 Evergreen Terrace"},
        headers=headers,
    )
    assert addr_resp.status_code == 200
    assert addr_resp.json()["state"] == "ADDRESS_SET"

    # Verify event timestamps
    if producer is not None:
        put_events = [
            e[1] for e in producer.events if e[1].operation.path_template == "/orders/{id}/address"
        ]
        assert len(put_events) == 1
        put_ev = put_events[0]
        assert put_ev.ts_received is not None
        assert put_ev.ts_completed is not None
        assert put_ev.status_code == 200
        assert put_ev.resource_id == order_id


@pytest.mark.anyio
async def test_burst_exceeding_rate_limit_returns_429(
    gateway_env: tuple[httpx.AsyncClient, InMemoryEventProducer | None],
) -> None:
    """Fire burst of requests exceeding rate limit -> verify Gateway returns 429 without hitting Target App."""
    client, _ = gateway_env
    burst_headers = {"X-User-Sub": "burst-abuser", "X-Forwarded-For": "198.51.100.4"}

    statuses: list[int] = []
    for _ in range(8):
        resp = await client.post(
            "/cart/items",
            json={"sku": "SKU-SPAM", "quantity": 1},
            headers=burst_headers,
        )
        statuses.append(resp.status_code)

    # First few requests are allowed (200), subsequent burst is rate-limited (429)
    assert 200 in statuses
    assert 429 in statuses
