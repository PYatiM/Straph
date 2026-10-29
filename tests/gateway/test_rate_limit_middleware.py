"""Unit tests for RateLimitMiddleware.

Verifies:
- Allowed requests proceed to destination route with HTTP 200 and standard
  X-RateLimit-Limit, X-RateLimit-Remaining, and X-RateLimit-Reset headers.
- Throttled requests immediately return HTTP 429 Too Many Requests with JSON body
  {"detail": "Rate limit exceeded"} and Retry-After header.
- Proper key extraction combining caller_sub and client IP:
  - From JWT Bearer token sub claim.
  - From request.state.sub.
  - From X-Forwarded-For or X-Real-IP.
  - Fallback to anonymous:127.0.0.1.
- Key partition isolation across users and IP addresses.
- Integration with InMemoryTokenBucketRateLimiter and RedisFixedWindowRateLimiter.
- Custom key extractor support.
"""

from __future__ import annotations

import base64
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock

import fakeredis
import httpx
import pytest
from fastapi import FastAPI
from starlette.requests import Request

from services.gateway.rate_limit_middleware import (
    RateLimitMiddleware,
    extract_caller_sub,
    extract_client_ip,
    extract_rate_limit_key,
)
from services.gateway.rate_limiter import (
    InMemoryTokenBucketRateLimiter,
    RedisFixedWindowRateLimiter,
)


def make_test_jwt(sub: str) -> str:
    """Create a minimal unverified test JWT token with sub claim."""
    header = base64.urlsafe_b64encode(b'{"alg":"none","typ":"JWT"}').rstrip(b"=").decode("ascii")
    payload = (
        base64.urlsafe_b64encode(json.dumps({"sub": sub}).encode("utf-8"))
        .rstrip(b"=")
        .decode("ascii")
    )
    return f"{header}.{payload}."


def create_test_app(
    limiter: Any,
    limit: int | None = None,
    key_extractor: Any = None,
) -> FastAPI:
    """Helper to assemble a FastAPI application decorated with RateLimitMiddleware."""
    app = FastAPI()
    app.add_middleware(
        RateLimitMiddleware,
        limiter=limiter,
        limit=limit,
        key_extractor=key_extractor,
    )

    @app.get("/items")
    async def get_items() -> dict[str, str]:
        return {"status": "ok", "resource": "items"}

    @app.post("/items")
    async def create_item() -> dict[str, str]:
        return {"status": "created"}

    return app


@asynccontextmanager
async def async_client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    """Context manager providing an AsyncClient connected to an in-memory ASGI app."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


# =============================================================================
# Helper Unit Tests
# =============================================================================


def test_extract_caller_sub_bearer_jwt() -> None:
    """Caller sub is extracted from Bearer JWT token."""
    token = make_test_jwt("alice_123")
    scope = {
        "type": "http",
        "headers": [(b"authorization", f"Bearer {token}".encode("ascii"))],
    }
    request = Request(scope)
    assert extract_caller_sub(request) == "alice_123"


def test_extract_caller_sub_anonymous_when_no_token() -> None:
    """Caller sub falls back to 'anonymous' when authorization is absent or invalid."""
    scope = {"type": "http", "headers": []}
    request = Request(scope)
    assert extract_caller_sub(request) == "anonymous"

    # Malformed bearer
    scope_bad = {"type": "http", "headers": [(b"authorization", b"Bearer invalid-jwt-here")]}
    assert extract_caller_sub(Request(scope_bad)) == "anonymous"


def test_extract_client_ip_headers() -> None:
    """Client IP is extracted from X-Forwarded-For or X-Real-IP."""
    # X-Forwarded-For with multiple proxy hops
    scope_xff = {
        "type": "http",
        "headers": [(b"x-forwarded-for", b"203.0.113.195, 10.0.0.1, 192.168.1.1")],
    }
    assert extract_client_ip(Request(scope_xff)) == "203.0.113.195"

    # X-Real-IP
    scope_real = {
        "type": "http",
        "headers": [(b"x-real-ip", b"198.51.100.42")],
    }
    assert extract_client_ip(Request(scope_real)) == "198.51.100.42"


def test_extract_rate_limit_key_composite() -> None:
    """Composite key formats as '{caller_sub}:{client_ip}'."""
    token = make_test_jwt("bob_77")
    scope = {
        "type": "http",
        "headers": [
            (b"authorization", f"Bearer {token}".encode("ascii")),
            (b"x-forwarded-for", b"192.0.2.100"),
        ],
    }
    assert extract_rate_limit_key(Request(scope)) == "bob_77:192.0.2.100"


# =============================================================================
# Middleware Allowed Request & Header Injection
# =============================================================================


@pytest.mark.asyncio
async def test_allowed_request_passes_and_injects_headers() -> None:
    """Allowed requests pass through with 200 OK and valid X-RateLimit headers."""
    limiter = InMemoryTokenBucketRateLimiter(capacity=3, refill_rate=1.0)
    app = create_test_app(limiter)

    token = make_test_jwt("user_ok")
    headers = {"Authorization": f"Bearer {token}", "X-Forwarded-For": "10.0.0.1"}

    async with async_client(app) as client:
        # 1st request
        resp1 = await client.get("/items", headers=headers)
        assert resp1.status_code == 200
        assert resp1.json() == {"status": "ok", "resource": "items"}
        assert resp1.headers["X-RateLimit-Limit"] == "3"
        assert resp1.headers["X-RateLimit-Remaining"] == "2"
        assert int(resp1.headers["X-RateLimit-Reset"]) > 0

        # 2nd request
        resp2 = await client.get("/items", headers=headers)
        assert resp2.status_code == 200
        assert resp2.headers["X-RateLimit-Remaining"] == "1"

        # 3rd request: uses last quota
        resp3 = await client.get("/items", headers=headers)
        assert resp3.status_code == 200
        assert resp3.headers["X-RateLimit-Remaining"] == "0"


# =============================================================================
# Middleware 429 Throttling & Retry-After
# =============================================================================


@pytest.mark.asyncio
async def test_throttled_request_returns_429_with_body_and_retry_after() -> None:
    """Throttled requests immediately return HTTP 429 with expected body and Retry-After."""
    limiter = InMemoryTokenBucketRateLimiter(capacity=2, refill_rate=1.0)
    app = create_test_app(limiter)

    token = make_test_jwt("user_throttle")
    headers = {"Authorization": f"Bearer {token}", "X-Forwarded-For": "192.168.1.50"}

    async with async_client(app) as client:
        # Exhaust 2 tokens
        await client.get("/items", headers=headers)
        await client.get("/items", headers=headers)

        # 3rd request must be blocked
        blocked = await client.get("/items", headers=headers)
        assert blocked.status_code == 429
        assert blocked.json() == {"detail": "Rate limit exceeded"}

        # Retry-After header must be present and >= 1
        assert "Retry-After" in blocked.headers
        retry_after = int(blocked.headers["Retry-After"])
        assert retry_after >= 1

        # Rate limit diagnostic headers still present
        assert blocked.headers["X-RateLimit-Limit"] == "2"
        assert blocked.headers["X-RateLimit-Remaining"] == "0"
        assert int(blocked.headers["X-RateLimit-Reset"]) > 0


# =============================================================================
# Key Partition Isolation
# =============================================================================


@pytest.mark.asyncio
async def test_key_isolation_between_different_users() -> None:
    """Exhausting quota for User A does not throttle User B on the same IP."""
    limiter = InMemoryTokenBucketRateLimiter(capacity=2, refill_rate=1.0)
    app = create_test_app(limiter)

    token_a = make_test_jwt("user_alpha")
    token_b = make_test_jwt("user_beta")
    common_ip = "203.0.113.88"

    async with async_client(app) as client:
        # Exhaust user_alpha
        await client.get(
            "/items", headers={"Authorization": f"Bearer {token_a}", "X-Forwarded-For": common_ip}
        )
        await client.get(
            "/items", headers={"Authorization": f"Bearer {token_a}", "X-Forwarded-For": common_ip}
        )
        resp_a = await client.get(
            "/items", headers={"Authorization": f"Bearer {token_a}", "X-Forwarded-For": common_ip}
        )
        assert resp_a.status_code == 429

        # user_beta has independent quota on the exact same IP
        resp_b = await client.get(
            "/items", headers={"Authorization": f"Bearer {token_b}", "X-Forwarded-For": common_ip}
        )
        assert resp_b.status_code == 200
        assert resp_b.headers["X-RateLimit-Remaining"] == "1"


@pytest.mark.asyncio
async def test_key_isolation_between_different_ips() -> None:
    """The same user subject receives independent limits across different client IPs."""
    limiter = InMemoryTokenBucketRateLimiter(capacity=1, refill_rate=1.0)
    app = create_test_app(limiter)

    token = make_test_jwt("user_roaming")

    async with async_client(app) as client:
        # Exhaust on IP 1
        r1 = await client.get(
            "/items", headers={"Authorization": f"Bearer {token}", "X-Forwarded-For": "10.0.0.1"}
        )
        assert r1.status_code == 200

        r1_blocked = await client.get(
            "/items", headers={"Authorization": f"Bearer {token}", "X-Forwarded-For": "10.0.0.1"}
        )
        assert r1_blocked.status_code == 429

        # Request from IP 2 succeeds
        r2 = await client.get(
            "/items", headers={"Authorization": f"Bearer {token}", "X-Forwarded-For": "10.0.0.2"}
        )
        assert r2.status_code == 200


# =============================================================================
# Custom Key Extractor & Overrides
# =============================================================================


@pytest.mark.asyncio
async def test_custom_key_extractor() -> None:
    """Custom key extractor overrides default caller_sub:client_ip scheme."""
    limiter = InMemoryTokenBucketRateLimiter(capacity=1, refill_rate=1.0)

    def api_key_extractor(request: Request) -> str:
        return request.headers.get("X-API-Key", "anonymous")

    app = create_test_app(limiter, key_extractor=api_key_extractor)

    async with async_client(app) as client:
        r1 = await client.get("/items", headers={"X-API-Key": "key_secret_123"})
        assert r1.status_code == 200

        r2 = await client.get("/items", headers={"X-API-Key": "key_secret_123"})
        assert r2.status_code == 429

        # Different API key succeeds
        r3 = await client.get("/items", headers={"X-API-Key": "key_secret_456"})
        assert r3.status_code == 200


@pytest.mark.asyncio
async def test_explicit_limit_override() -> None:
    """Explicit limit argument overrides default limiter resolution in headers."""
    limiter = InMemoryTokenBucketRateLimiter(capacity=5, refill_rate=1.0)
    # Explicitly display limit as 100
    app = create_test_app(limiter, limit=100)

    async with async_client(app) as client:
        resp = await client.get("/items")
        assert resp.status_code == 200
        assert resp.headers["X-RateLimit-Limit"] == "100"


# =============================================================================
# Integration with RedisFixedWindowRateLimiter
# =============================================================================


@pytest.mark.asyncio
async def test_middleware_with_redis_rate_limiter() -> None:
    """RateLimitMiddleware operates seamlessly with RedisFixedWindowRateLimiter."""
    fake_redis = fakeredis.FakeAsyncRedis()
    limiter = RedisFixedWindowRateLimiter(fake_redis, max_requests=2, window_sec=60)
    app = create_test_app(limiter)

    token = make_test_jwt("redis_user")
    headers = {"Authorization": f"Bearer {token}", "X-Real-IP": "172.16.0.5"}

    async with async_client(app) as client:
        # Request 1
        r1 = await client.get("/items", headers=headers)
        assert r1.status_code == 200
        assert r1.headers["X-RateLimit-Limit"] == "2"
        assert r1.headers["X-RateLimit-Remaining"] == "1"

        # Request 2
        r2 = await client.get("/items", headers=headers)
        assert r2.status_code == 200
        assert r2.headers["X-RateLimit-Remaining"] == "0"

        # Request 3 is blocked
        r3 = await client.get("/items", headers=headers)
        assert r3.status_code == 429
        assert r3.json() == {"detail": "Rate limit exceeded"}
        assert "Retry-After" in r3.headers


# =============================================================================
# Graceful Fallback on Limiter Error
# =============================================================================


@pytest.mark.asyncio
async def test_limiter_exception_fails_open() -> None:
    """If limiter raises an unhandled error, middleware fails open allowing traffic."""
    broken_limiter = AsyncMock()
    broken_limiter.check_rate_limit.side_effect = RuntimeError("Storage outage")
    broken_limiter.capacity = 10

    app = create_test_app(broken_limiter)

    async with async_client(app) as client:
        resp = await client.get("/items")
        assert resp.status_code == 200
        assert resp.headers["X-RateLimit-Limit"] == "10"
