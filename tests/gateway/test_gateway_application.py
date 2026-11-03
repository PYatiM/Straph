"""Integration tests for the assembled Gateway application."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from jsonschema import Draft7Validator

from services.evaluation.target_app.app import create_app as create_target_app
from services.gateway.app import create_gateway_app
from services.gateway.producer import InMemoryEventProducer
from services.gateway.rate_limiter import InMemoryTokenBucketRateLimiter

ROOT_DIR = Path(__file__).resolve().parents[2]
EVENT_SCHEMA_PATH = ROOT_DIR / "contracts" / "event.schema.json"


@pytest.fixture
def event_validator() -> Draft7Validator:
    with open(EVENT_SCHEMA_PATH, "r", encoding="utf-8") as f:
        schema = json.load(f)
    return Draft7Validator(schema)


@pytest.fixture
def in_memory_producer() -> InMemoryEventProducer:
    return InMemoryEventProducer()


@pytest.fixture
def rate_limiter() -> InMemoryTokenBucketRateLimiter:
    return InMemoryTokenBucketRateLimiter(capacity=10, refill_rate=10.0)


@pytest.fixture
def target_app() -> Any:
    return create_target_app(strict=True)


@pytest.fixture
async def gateway_client(
    target_app: Any,
    in_memory_producer: InMemoryEventProducer,
    rate_limiter: InMemoryTokenBucketRateLimiter,
) -> httpx.AsyncClient:
    target_transport = httpx.ASGITransport(app=target_app)
    downstream_client = httpx.AsyncClient(
        transport=target_transport,
        base_url="http://target-app:8001",
    )

    gw_app = create_gateway_app(
        target_app_url="http://target-app:8001",
        producer=in_memory_producer,
        rate_limiter=rate_limiter,
        client=downstream_client,
    )

    gw_transport = httpx.ASGITransport(app=gw_app)
    async with httpx.AsyncClient(transport=gw_transport, base_url="http://gateway:8000") as client:
        yield client


@pytest.mark.anyio
async def test_health_endpoint_healthy(gateway_client: httpx.AsyncClient) -> None:
    """Verify /health returns 200 with gateway and downstream status."""
    resp = await gateway_client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "healthy"
    assert data["gateway"] == "running"
    assert data["downstream"] == "healthy"


@pytest.mark.anyio
async def test_health_endpoint_downstream_unavailable() -> None:
    """Verify /health reports degraded when downstream is unreachable."""
    failing_client = httpx.AsyncClient(base_url="http://127.0.0.1:9999")
    gw_app = create_gateway_app(
        target_app_url="http://127.0.0.1:9999",
        client=failing_client,
    )
    transport = httpx.ASGITransport(app=gw_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://gateway:8000") as client:
        resp = await client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "degraded"
        assert data["downstream"] == "unavailable"


@pytest.mark.anyio
async def test_proxied_order_creation_and_event_emission(
    gateway_client: httpx.AsyncClient,
    in_memory_producer: InMemoryEventProducer,
    event_validator: Draft7Validator,
) -> None:
    """Verify reverse proxy forwards order creation, returns body, adds headers, and emits valid event."""
    headers = {"X-User-Sub": "customer-42", "X-Session-Id": "sess-test-1"}

    # 1. Add item to cart first (target app requires non-empty cart to create order)
    cart_resp = await gateway_client.post(
        "/cart/items",
        json={"sku": "SKU-BOOK-01", "quantity": 2},
        headers=headers,
    )
    assert cart_resp.status_code == 200

    # 2. Create order from cart
    resp = await gateway_client.post("/orders", headers=headers)
    assert resp.status_code == 201
    body = resp.json()
    assert "order_id" in body
    order_id = body["order_id"]

    # Verify rate limit headers injected by RateLimitMiddleware
    assert "X-RateLimit-Limit" in resp.headers
    assert "X-RateLimit-Remaining" in resp.headers
    assert "X-RateLimit-Reset" in resp.headers

    # Verify event emission to producer (events: cart addition + order creation)
    events = in_memory_producer.events
    assert len(events) == 2
    key, event = events[1]

    assert key == f"orders:{order_id}"
    assert event.resource_type == "orders"
    assert event.resource_id == order_id
    assert event.status_code == 201
    assert event.operation.method == "POST"
    assert event.operation.path_template == "/orders"
    assert event.session_id == "sess-test-1"

    # Schema validation against contracts/event.schema.json
    event_dict = event.model_dump()
    errors = list(event_validator.iter_errors(event_dict))
    assert errors == []


@pytest.mark.anyio
async def test_proxied_address_update_path_template(
    gateway_client: httpx.AsyncClient,
    in_memory_producer: InMemoryEventProducer,
    event_validator: Draft7Validator,
) -> None:
    """Verify path with ID normalizes path_template to /orders/{id}/address in emitted event."""
    headers = {"X-User-Sub": "cust-1"}

    # Add item to cart
    await gateway_client.post(
        "/cart/items",
        json={"sku": "SKU-1", "quantity": 1},
        headers=headers,
    )

    # Create order
    create_resp = await gateway_client.post("/orders", headers=headers)
    assert create_resp.status_code == 201
    order_id = create_resp.json()["order_id"]

    # Now update address
    update_resp = await gateway_client.put(
        f"/orders/{order_id}/address",
        json={"address": "123 Main St"},
        headers=headers,
    )
    assert update_resp.status_code == 200

    # Verify emitted event for the PUT
    events = in_memory_producer.events
    assert len(events) == 3
    put_event = events[2][1]

    assert put_event.operation.method == "PUT"
    assert put_event.operation.path_template == "/orders/{id}/address"
    assert put_event.resource_type == "orders"
    assert put_event.resource_id == order_id

    errors = list(event_validator.iter_errors(put_event.model_dump()))
    assert errors == []


@pytest.mark.anyio
async def test_rate_limiting_enforcement(
    target_app: Any,
    in_memory_producer: InMemoryEventProducer,
) -> None:
    """Verify gateway returns 429 when quota is exhausted."""
    strict_limiter = InMemoryTokenBucketRateLimiter(capacity=2, refill_rate=0.1)
    target_transport = httpx.ASGITransport(app=target_app)
    downstream_client = httpx.AsyncClient(
        transport=target_transport,
        base_url="http://target-app:8001",
    )

    gw_app = create_gateway_app(
        target_app_url="http://target-app:8001",
        producer=in_memory_producer,
        rate_limiter=strict_limiter,
        client=downstream_client,
    )
    transport = httpx.ASGITransport(app=gw_app)

    async with httpx.AsyncClient(transport=transport, base_url="http://gateway:8000") as client:
        # Request 1: allowed
        r1 = await client.post(
            "/cart/items",
            json={"sku": "SKU-BURST", "quantity": 1},
            headers={"X-User-Sub": "user-burst"},
        )
        assert r1.status_code == 200

        # Request 2: allowed
        r2 = await client.post(
            "/cart/items",
            json={"sku": "SKU-BURST", "quantity": 1},
            headers={"X-User-Sub": "user-burst"},
        )
        assert r2.status_code == 200

        # Request 3: rate limited
        r3 = await client.post(
            "/cart/items",
            json={"sku": "SKU-BURST", "quantity": 1},
            headers={"X-User-Sub": "user-burst"},
        )
        assert r3.status_code == 429
        assert "Retry-After" in r3.headers
        assert r3.json()["detail"] == "Rate limit exceeded"
