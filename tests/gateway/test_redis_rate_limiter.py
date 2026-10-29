"""Unit tests for RedisFixedWindowRateLimiter using fakeredis.

Verifies:
- Conformance to RateLimiterProtocol.
- Atomic counter increment and burst allowance up to max_requests.
- Request throttling when counter exceeds limit.
- Automatic window expiration and quota replenishment.
- Atomicity under concurrent bursts preventing limit bypass.
- Graceful fail-open fallback with logged warnings on Redis errors/unreachability.
- Key partition isolation and input validation.
"""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock

import fakeredis
import pytest
from redis.exceptions import (
    ConnectionError as RedisConnectionError,
)
from redis.exceptions import (
    TimeoutError as RedisTimeoutError,
)

from services.gateway.rate_limiter import (
    RateLimiterProtocol,
    RateLimiterValidationError,
    RedisFixedWindowRateLimiter,
)


@pytest.fixture
def fake_redis() -> fakeredis.FakeAsyncRedis:
    """Fixture providing a fresh FakeAsyncRedis client."""
    return fakeredis.FakeAsyncRedis()


# =============================================================================
# Protocol Conformance
# =============================================================================


def test_redis_limiter_protocol_conformance(fake_redis: fakeredis.FakeAsyncRedis) -> None:
    """RedisFixedWindowRateLimiter conforms to RateLimiterProtocol."""
    limiter = RedisFixedWindowRateLimiter(fake_redis, max_requests=10, window_sec=60)
    assert isinstance(limiter, RateLimiterProtocol)


# =============================================================================
# Atomic Increment & Throttling
# =============================================================================


@pytest.mark.asyncio
async def test_burst_allowance_up_to_max_requests(
    fake_redis: fakeredis.FakeAsyncRedis,
) -> None:
    """Requests up to max_requests are allowed with decremented remaining quotas."""
    limiter = RedisFixedWindowRateLimiter(fake_redis, max_requests=5, window_sec=60)

    # 1st request
    allowed, remaining, reset_epoch = await limiter.check_rate_limit("client_1")
    assert allowed is True
    assert remaining == 4
    assert reset_epoch > 0

    # 2nd request
    allowed, remaining, _ = await limiter.check_rate_limit("client_1")
    assert allowed is True
    assert remaining == 3

    # 3rd request
    allowed, remaining, _ = await limiter.check_rate_limit("client_1")
    assert allowed is True
    assert remaining == 2

    # 4th request
    allowed, remaining, _ = await limiter.check_rate_limit("client_1")
    assert allowed is True
    assert remaining == 1

    # 5th request: consumes last token in window
    allowed, remaining, _ = await limiter.check_rate_limit("client_1")
    assert allowed is True
    assert remaining == 0

    # Redis counter should equal 5
    count = await limiter.get_current_count("client_1")
    assert count == 5


@pytest.mark.asyncio
async def test_subsequent_throttling_beyond_limit(
    fake_redis: fakeredis.FakeAsyncRedis,
) -> None:
    """Requests exceeding max_requests are throttled."""
    limiter = RedisFixedWindowRateLimiter(fake_redis, max_requests=3, window_sec=60)

    # Exhaust limit of 3
    for _ in range(3):
        allowed, _, _ = await limiter.check_rate_limit("client_2")
        assert allowed is True

    # 4th and 5th requests are throttled
    allowed4, remaining4, _ = await limiter.check_rate_limit("client_2")
    allowed5, remaining5, _ = await limiter.check_rate_limit("client_2")

    assert allowed4 is False
    assert remaining4 == 0
    assert allowed5 is False
    assert remaining5 == 0

    # Counter in Redis continues incrementing to track total hit volume
    count = await limiter.get_current_count("client_2")
    assert count == 5


# =============================================================================
# Key Expiration & TTL
# =============================================================================


@pytest.mark.asyncio
async def test_ttl_set_on_first_hit(fake_redis: fakeredis.FakeAsyncRedis) -> None:
    """Lua script sets TTL only on the first increment (when count == 1)."""
    limiter = RedisFixedWindowRateLimiter(fake_redis, max_requests=5, window_sec=30)

    # Before first hit, key does not exist
    ttl_before = await limiter.get_ttl("client_ttl")
    assert ttl_before == -2

    # First hit creates key and sets TTL
    await limiter.check_rate_limit("client_ttl")
    ttl_after = await limiter.get_ttl("client_ttl")
    assert 0 < ttl_after <= 30

    # Second hit should keep roughly the same TTL
    await limiter.check_rate_limit("client_ttl")
    ttl_second = await limiter.get_ttl("client_ttl")
    assert 0 < ttl_second <= ttl_after


@pytest.mark.asyncio
async def test_window_expiration_replenishes_quota(
    fake_redis: fakeredis.FakeAsyncRedis,
) -> None:
    """When the fixed window expires, the counter resets and quota is restored."""
    limiter = RedisFixedWindowRateLimiter(fake_redis, max_requests=2, window_sec=1)

    # Exhaust 2 requests
    allowed1, _, _ = await limiter.check_rate_limit("client_expire")
    allowed2, _, _ = await limiter.check_rate_limit("client_expire")
    assert allowed1 is True
    assert allowed2 is True

    # 3rd request throttled
    allowed3, _, _ = await limiter.check_rate_limit("client_expire")
    assert allowed3 is False

    # Wait for window expiration (1.1s > 1s window)
    await asyncio.sleep(1.1)

    # 4th request in new window is allowed
    allowed4, remaining4, _ = await limiter.check_rate_limit("client_expire")
    assert allowed4 is True
    assert remaining4 == 1


# =============================================================================
# Concurrency & Atomicity
# =============================================================================


@pytest.mark.asyncio
async def test_concurrent_burst_cannot_bypass_limit(
    fake_redis: fakeredis.FakeAsyncRedis,
) -> None:
    """Lua script atomicity ensures bursts cannot bypass the window limit."""
    limiter = RedisFixedWindowRateLimiter(fake_redis, max_requests=10, window_sec=60)

    # Dispatch 50 concurrent checks
    tasks = [limiter.check_rate_limit("burst_key") for _ in range(50)]
    results = await asyncio.gather(*tasks)

    allowed_results = [res for res in results if res[0] is True]
    throttled_results = [res for res in results if res[0] is False]

    # Exactly 10 allowed and 40 throttled
    assert len(allowed_results) == 10
    assert len(throttled_results) == 40

    # Monotonic remaining quotas from 9 down to 0
    quotas = sorted(res[1] for res in allowed_results)
    assert quotas == list(range(10))

    # All throttled have 0 remaining quota
    assert all(res[1] == 0 for res in throttled_results)

    # Total count in Redis is 50
    assert await limiter.get_current_count("burst_key") == 50


# =============================================================================
# Graceful Fallback & Connection Errors (Fail Open)
# =============================================================================


@pytest.mark.asyncio
async def test_redis_connection_error_fails_open_with_logged_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Connection errors fail open (allowed=True) and log a warning."""
    broken_redis = AsyncMock()
    del broken_redis.register_script
    broken_redis.eval.side_effect = RedisConnectionError("Connection refused by peer")

    limiter = RedisFixedWindowRateLimiter(broken_redis, max_requests=5, window_sec=60)

    with caplog.at_level(logging.WARNING):
        allowed, remaining, reset_epoch = await limiter.check_rate_limit("unreachable_key")

    assert allowed is True
    assert remaining == 5
    assert reset_epoch > 0
    assert "Redis rate limiter unreachable" in caplog.text
    assert "unreachable_key" in caplog.text


@pytest.mark.asyncio
async def test_redis_timeout_error_fails_open_with_logged_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Timeout errors fail open safely with a logged warning."""
    broken_redis = AsyncMock()
    del broken_redis.register_script
    broken_redis.eval.side_effect = RedisTimeoutError("Command execution timed out")

    limiter = RedisFixedWindowRateLimiter(broken_redis, max_requests=10, window_sec=30)

    with caplog.at_level(logging.WARNING):
        allowed, remaining, _ = await limiter.check_rate_limit("timeout_key")

    assert allowed is True
    assert remaining == 10
    assert "Redis rate limiter unreachable" in caplog.text


@pytest.mark.asyncio
async def test_os_error_fails_open_with_logged_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Network OSError fails open safely with a logged warning."""
    broken_redis = AsyncMock()
    del broken_redis.register_script
    broken_redis.eval.side_effect = OSError("Network is unreachable")

    limiter = RedisFixedWindowRateLimiter(broken_redis, max_requests=20, window_sec=60)

    with caplog.at_level(logging.WARNING):
        allowed, remaining, _ = await limiter.check_rate_limit("os_error_key")

    assert allowed is True
    assert remaining == 20
    assert "Redis rate limiter unreachable" in caplog.text


# =============================================================================
# Key Partition Isolation & Reset
# =============================================================================


@pytest.mark.asyncio
async def test_key_isolation(fake_redis: fakeredis.FakeAsyncRedis) -> None:
    """Exhausting quota for one partition key does not affect other keys."""
    limiter = RedisFixedWindowRateLimiter(fake_redis, max_requests=2, window_sec=60)

    # Exhaust tenant_a
    await limiter.check_rate_limit("tenant_a")
    await limiter.check_rate_limit("tenant_a")
    assert (await limiter.check_rate_limit("tenant_a"))[0] is False

    # tenant_b is untouched
    allowed_b, remaining_b, _ = await limiter.check_rate_limit("tenant_b")
    assert allowed_b is True
    assert remaining_b == 1


@pytest.mark.asyncio
async def test_reset_key_and_all_keys(fake_redis: fakeredis.FakeAsyncRedis) -> None:
    """Specific keys and all keys under prefix can be cleared."""
    limiter = RedisFixedWindowRateLimiter(fake_redis, max_requests=1, window_sec=60)

    await limiter.check_rate_limit("key_1")
    await limiter.check_rate_limit("key_2")

    assert (await limiter.check_rate_limit("key_1"))[0] is False
    assert (await limiter.check_rate_limit("key_2"))[0] is False

    # Reset key_1 only
    await limiter.reset("key_1")
    assert (await limiter.check_rate_limit("key_1"))[0] is True
    assert (await limiter.check_rate_limit("key_2"))[0] is False

    # Reset all keys
    await limiter.reset()
    assert (await limiter.check_rate_limit("key_2"))[0] is True


# =============================================================================
# Validation Errors
# =============================================================================


def test_invalid_parameters_raise_validation_error(fake_redis: fakeredis.FakeAsyncRedis) -> None:
    """Invalid client, max_requests, or window_sec raise RateLimiterValidationError."""
    with pytest.raises(RateLimiterValidationError):
        RedisFixedWindowRateLimiter(None, max_requests=10, window_sec=60)  # type: ignore[arg-type]

    for invalid in [0, -1, 10.5, "10", True, False, None]:
        with pytest.raises(RateLimiterValidationError):
            RedisFixedWindowRateLimiter(fake_redis, max_requests=invalid, window_sec=60)  # type: ignore[arg-type]

    for invalid in [0, -5, 60.0, "60", True, False, None]:
        with pytest.raises(RateLimiterValidationError):
            RedisFixedWindowRateLimiter(fake_redis, max_requests=10, window_sec=invalid)  # type: ignore[arg-type]

    with pytest.raises(RateLimiterValidationError):
        RedisFixedWindowRateLimiter(fake_redis, max_requests=10, window_sec=60, key_prefix=123)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_invalid_key_raises_validation_error(fake_redis: fakeredis.FakeAsyncRedis) -> None:
    """Empty or non-string key raises RateLimiterValidationError."""
    limiter = RedisFixedWindowRateLimiter(fake_redis, max_requests=10, window_sec=60)

    for invalid in ["", "   ", "\n", None, 123, ["key"]]:
        with pytest.raises(RateLimiterValidationError):
            await limiter.check_rate_limit(invalid)  # type: ignore[arg-type]
