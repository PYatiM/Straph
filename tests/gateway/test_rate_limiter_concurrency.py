"""Concurrency unit tests for rate limiting under extreme parallel request bursts.

Verifies:
- Atomic Lua script execution in RedisFixedWindowRateLimiter strictly guarantees
  that at most limit (10) requests return HTTP 200 and remaining (40) return HTTP 429.
- Negative test against a deliberately broken non-atomic (GET then INCR) mock limiter
  demonstrates and catches TOCTOU (Time-of-Check to Time-of-Use) race conditions.
- InMemoryTokenBucketRateLimiter with per-key asyncio.Lock similarly guarantees
  strict concurrency protection.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import fakeredis
import httpx
import pytest
from fastapi import FastAPI

from services.gateway.rate_limit_middleware import RateLimitMiddleware
from services.gateway.rate_limiter import (
    InMemoryTokenBucketRateLimiter,
    RateLimiterProtocol,
    RedisFixedWindowRateLimiter,
)


class BrokenNonAtomicRateLimiter:
    """Deliberately broken non-atomic rate limiter suffering from TOCTOU race conditions.

    Executes a separate GET (Time-Of-Check) followed by a delayed INCR (Time-Of-Use),
    allowing concurrent requests to observe an outdated counter value and bypass limits.
    """

    def __init__(
        self,
        redis_client: Any,
        max_requests: int = 10,
        window_sec: int = 10,
    ) -> None:
        self.redis_client: Any = redis_client
        self.max_requests: int = max_requests
        self.window_sec: int = window_sec

    async def check_rate_limit(self, key: str) -> tuple[bool, int, float]:
        redis_key = f"broken_rate_limit:{key}"

        # 1. Non-atomic read: Time-of-Check
        val = await self.redis_client.get(redis_key)
        current_count = int(val) if val is not None else 0

        # Yield control to event loop to simulate realistic async I/O interleaving
        await asyncio.sleep(0.001)

        # 2. Non-atomic write: Time-of-Use
        if current_count < self.max_requests:
            new_val = await self.redis_client.incr(redis_key)
            if int(new_val) == 1:
                await self.redis_client.expire(redis_key, self.window_sec)
            allowed = True
            remaining_quota = max(0, self.max_requests - int(new_val))
        else:
            allowed = False
            remaining_quota = 0

        reset_epoch_sec = time.time() + float(self.window_sec)
        return (allowed, remaining_quota, reset_epoch_sec)


def create_gateway_app(limiter: RateLimiterProtocol) -> FastAPI:
    """Instantiate a FastAPI application wrapped with RateLimitMiddleware."""
    app = FastAPI()
    app.add_middleware(RateLimitMiddleware, limiter=limiter)

    @app.get("/api/v1/resource")
    async def get_resource() -> dict[str, str]:
        return {"status": "success", "message": "resource accessed"}

    return app


@asynccontextmanager
async def async_client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    """Provide an AsyncClient configured for in-memory ASGI dispatch."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


# =============================================================================
# Positive Concurrency Test: Atomic Lua Rate Limiter
# =============================================================================


@pytest.mark.asyncio
async def test_atomic_lua_rate_limiter_under_concurrent_burst() -> None:
    """Atomic Lua script guarantees exactly 10 requests allowed and 40 throttled."""
    fake_redis = fakeredis.FakeAsyncRedis()
    # 1. Set rate limiter limit to 10 requests per 10-second window
    limiter = RedisFixedWindowRateLimiter(fake_redis, max_requests=10, window_sec=10)
    app = create_gateway_app(limiter)

    headers = {
        "X-Caller-Sub": "concurrent_tester",
        "X-Forwarded-For": "198.51.100.25",
    }

    async with async_client(app) as client:
        # 2. Fire 50 concurrent requests simultaneously at the gateway test endpoint
        tasks = [client.get("/api/v1/resource", headers=headers) for _ in range(50)]
        responses = await asyncio.gather(*tasks)

    # 3. Assert that exactly 10 requests return HTTP 200 and exactly 40 return HTTP 429
    status_200_responses = [r for r in responses if r.status_code == 200]
    status_429_responses = [r for r in responses if r.status_code == 429]

    assert len(status_200_responses) == 10, (
        f"Expected exactly 10 HTTP 200 responses, got {len(status_200_responses)}"
    )
    assert len(status_429_responses) == 40, (
        f"Expected exactly 40 HTTP 429 responses, got {len(status_429_responses)}"
    )

    # Validate 200 OK responses
    for resp in status_200_responses:
        assert resp.json() == {"status": "success", "message": "resource accessed"}
        assert resp.headers["X-RateLimit-Limit"] == "10"
        assert int(resp.headers["X-RateLimit-Remaining"]) >= 0
        assert int(resp.headers["X-RateLimit-Reset"]) > 0

    # Remaining quotas of allowed requests must span 0 through 9
    remaining_quotas = sorted(int(r.headers["X-RateLimit-Remaining"]) for r in status_200_responses)
    assert remaining_quotas == list(range(10))

    # Validate 429 responses
    for resp in status_429_responses:
        assert resp.json() == {"detail": "Rate limit exceeded"}
        assert "Retry-After" in resp.headers
        assert int(resp.headers["Retry-After"]) >= 1
        assert resp.headers["X-RateLimit-Limit"] == "10"
        assert resp.headers["X-RateLimit-Remaining"] == "0"


# =============================================================================
# Negative Concurrency Test: Deliberately Broken Non-Atomic Limiter (TOCTOU)
# =============================================================================


@pytest.mark.asyncio
async def test_non_atomic_limiter_demonstrates_toctou_race_condition() -> None:
    """Negative test: Non-atomic (GET then INCR) limiter allows >10 requests under burst.

    Proves that our test suite actively detects TOCTOU vulnerabilities and confirms
    that the atomic Lua implementation is required to prevent limit bypasses.
    """
    fake_redis = fakeredis.FakeAsyncRedis()
    broken_limiter = BrokenNonAtomicRateLimiter(fake_redis, max_requests=10, window_sec=10)
    app = create_gateway_app(broken_limiter)  # type: ignore[arg-type]

    headers = {
        "X-Caller-Sub": "toctou_exploiter",
        "X-Forwarded-For": "198.51.100.99",
    }

    async with async_client(app) as client:
        # Fire 50 concurrent requests against the non-atomic limiter
        tasks = [client.get("/api/v1/resource", headers=headers) for _ in range(50)]
        responses = await asyncio.gather(*tasks)

    status_200_count = sum(1 for r in responses if r.status_code == 200)
    status_429_count = sum(1 for r in responses if r.status_code == 429)

    # In a non-atomic limiter, the TOCTOU window allows significantly more than 10 requests
    assert status_200_count > 10, (
        f"Expected non-atomic limiter to suffer TOCTOU race and allow >10 requests, "
        f"but only {status_200_count} succeeded."
    )
    assert status_429_count < 40, (
        f"Expected non-atomic limiter to throttle <40 requests, but got {status_429_count}"
    )


# =============================================================================
# In-Memory Concurrency Verification
# =============================================================================


@pytest.mark.asyncio
async def test_in_memory_token_bucket_under_concurrent_burst() -> None:
    """InMemoryTokenBucketRateLimiter with per-key lock permits exactly 10 requests."""
    # Refill rate negligible to avoid refill during immediate 50 concurrent burst
    limiter = InMemoryTokenBucketRateLimiter(capacity=10, refill_rate=1e-6)
    app = create_gateway_app(limiter)

    headers = {
        "X-Caller-Sub": "in_memory_tester",
        "X-Forwarded-For": "10.10.10.10",
    }

    async with async_client(app) as client:
        tasks = [client.get("/api/v1/resource", headers=headers) for _ in range(50)]
        responses = await asyncio.gather(*tasks)

    status_200_count = sum(1 for r in responses if r.status_code == 200)
    status_429_count = sum(1 for r in responses if r.status_code == 429)

    assert status_200_count == 10
    assert status_429_count == 40
