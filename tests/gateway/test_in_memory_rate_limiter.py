"""Unit tests for InMemoryTokenBucketRateLimiter and RateLimiterProtocol.

Verifies:
- Conformance to RateLimiterProtocol with runtime type checking.
- Burst allowance up to configured capacity.
- Subsequent throttling when tokens are exhausted.
- Continuous token replenishment over simulated time.
- Prevention of race conditions across concurrent asynchronous requests.
- Key partition isolation (requests on one key do not impact another).
- Input parameter validation for capacity, refill_rate, keys, and costs.
- Key reset functionality for single keys and across the entire store.
"""

from __future__ import annotations

import asyncio

import pytest

from services.gateway.rate_limiter import (
    InMemoryTokenBucketRateLimiter,
    RateLimiterProtocol,
    RateLimiterValidationError,
)


class MockClock:
    """Controllable clock for deterministic time-based rate limit tests."""

    def __init__(self, start: float = 1_000_000.0) -> None:
        self.current_time: float = float(start)

    def __call__(self) -> float:
        return self.current_time

    def advance(self, seconds: float) -> None:
        self.current_time += seconds


# =============================================================================
# Protocol Conformance
# =============================================================================


def test_rate_limiter_protocol_conformance() -> None:
    """InMemoryTokenBucketRateLimiter satisfies RateLimiterProtocol."""
    limiter = InMemoryTokenBucketRateLimiter(capacity=10, refill_rate=1.0)
    assert isinstance(limiter, RateLimiterProtocol)


def test_rate_limiter_protocol_rejects_non_conforming_classes() -> None:
    """Objects without check_rate_limit do not satisfy RateLimiterProtocol."""

    class IncompleteLimiter:
        pass

    assert not isinstance(IncompleteLimiter(), RateLimiterProtocol)


# =============================================================================
# Burst Allowance & Subsequent Throttling
# =============================================================================


@pytest.mark.asyncio
async def test_burst_allowance_up_to_capacity() -> None:
    """Requests are allowed up to full capacity in immediate succession."""
    clock = MockClock(100.0)
    limiter = InMemoryTokenBucketRateLimiter(capacity=5, refill_rate=1.0, time_func=clock)

    # 1st request: remaining should be 4
    allowed, remaining, reset_time = await limiter.check_rate_limit("client_1")
    assert allowed is True
    assert remaining == 4
    assert reset_time == pytest.approx(101.0)

    # 2nd request: remaining should be 3
    allowed, remaining, reset_time = await limiter.check_rate_limit("client_1")
    assert allowed is True
    assert remaining == 3
    assert reset_time == pytest.approx(102.0)

    # 3rd request: remaining should be 2
    allowed, remaining, reset_time = await limiter.check_rate_limit("client_1")
    assert allowed is True
    assert remaining == 2
    assert reset_time == pytest.approx(103.0)

    # 4th request: remaining should be 1
    allowed, remaining, reset_time = await limiter.check_rate_limit("client_1")
    assert allowed is True
    assert remaining == 1
    assert reset_time == pytest.approx(104.0)

    # 5th request: bucket exhausted, remaining should be 0
    allowed, remaining, reset_time = await limiter.check_rate_limit("client_1")
    assert allowed is True
    assert remaining == 0
    assert reset_time == pytest.approx(105.0)


@pytest.mark.asyncio
async def test_subsequent_throttling_when_bucket_empty() -> None:
    """Requests beyond capacity are throttled until tokens replenish."""
    clock = MockClock(200.0)
    limiter = InMemoryTokenBucketRateLimiter(capacity=3, refill_rate=1.0, time_func=clock)

    # Consume full capacity of 3
    for _ in range(3):
        allowed, _, _ = await limiter.check_rate_limit("client_2")
        assert allowed is True

    # 4th request at the same timestamp must be throttled
    allowed, remaining, reset_time = await limiter.check_rate_limit("client_2")
    assert allowed is False
    assert remaining == 0
    assert reset_time == pytest.approx(203.0)

    # 5th request at the same timestamp remains throttled
    allowed, remaining, reset_time = await limiter.check_rate_limit("client_2")
    assert allowed is False
    assert remaining == 0
    assert reset_time == pytest.approx(203.0)


# =============================================================================
# Refill Over Time
# =============================================================================


@pytest.mark.asyncio
async def test_continuous_refill_replenishes_quota() -> None:
    """Bucket replenishes tokens continuously proportional to elapsed time."""
    clock = MockClock(10.0)
    # 2 tokens per second, max 4 tokens
    limiter = InMemoryTokenBucketRateLimiter(capacity=4, refill_rate=2.0, time_func=clock)

    # Exhaust all 4 tokens
    for _ in range(4):
        allowed, _, _ = await limiter.check_rate_limit("client_refill")
        assert allowed is True

    # Immediate check is throttled
    allowed, remaining, _ = await limiter.check_rate_limit("client_refill")
    assert allowed is False
    assert remaining == 0

    # Advance clock by 0.25s: 0.5 tokens refilled (< 1.0) -> still throttled
    clock.advance(0.25)
    allowed, remaining, _ = await limiter.check_rate_limit("client_refill")
    assert allowed is False
    assert remaining == 0

    # Advance clock by another 0.25s (total 0.5s -> 1.0 token total) -> allowed
    clock.advance(0.25)
    allowed, remaining, _ = await limiter.check_rate_limit("client_refill")
    assert allowed is True
    assert remaining == 0

    # Advance clock by 1.0s -> 2 tokens refilled
    clock.advance(1.0)
    allowed1, remaining1, _ = await limiter.check_rate_limit("client_refill")
    allowed2, remaining2, _ = await limiter.check_rate_limit("client_refill")
    assert allowed1 is True
    assert remaining1 == 1
    assert allowed2 is True
    assert remaining2 == 0

    # Advance clock by 100s -> tokens do not exceed capacity (4)
    clock.advance(100.0)
    tokens = await limiter.get_remaining_tokens("client_refill")
    assert tokens == pytest.approx(4.0)


@pytest.mark.asyncio
async def test_real_time_rate_limiting_with_asyncio_sleep() -> None:
    """Verifies throttling and replenishment using real wall-clock time and sleep."""
    # 20 tokens/sec -> 1 token every 50ms
    limiter = InMemoryTokenBucketRateLimiter(capacity=2, refill_rate=20.0)

    # Burst of 2
    allowed1, _, _ = await limiter.check_rate_limit("realtime_key")
    allowed2, _, _ = await limiter.check_rate_limit("realtime_key")
    assert allowed1 is True
    assert allowed2 is True

    # 3rd is throttled
    allowed3, _, _ = await limiter.check_rate_limit("realtime_key")
    assert allowed3 is False

    # Sleep for ~65ms to allow at least 1 token (50ms) to refill
    await asyncio.sleep(0.065)

    # 4th is allowed
    allowed4, _, _ = await limiter.check_rate_limit("realtime_key")
    assert allowed4 is True


# =============================================================================
# Concurrency & Race Condition Prevention
# =============================================================================


@pytest.mark.asyncio
async def test_concurrent_async_checks_prevent_race_conditions() -> None:
    """Concurrent asynchronous checks on the same key permit exactly capacity requests."""
    clock = MockClock(500.0)
    # Refill rate negligible so no refill occurs during the concurrent burst
    limiter = InMemoryTokenBucketRateLimiter(capacity=10, refill_rate=1e-6, time_func=clock)

    # Spawn 50 concurrent checks on the exact same key
    tasks = [limiter.check_rate_limit("concurrent_key") for _ in range(50)]
    results = await asyncio.gather(*tasks)

    allowed_results = [res for res in results if res[0] is True]
    throttled_results = [res for res in results if res[0] is False]

    # Exactly capacity (10) allowed and 40 throttled
    assert len(allowed_results) == 10
    assert len(throttled_results) == 40

    # Allowed remaining quotas must span 0 through 9
    remaining_quotas = sorted(res[1] for res in allowed_results)
    assert remaining_quotas == list(range(10))

    # All throttled calls have remaining quota 0
    assert all(res[1] == 0 for res in throttled_results)


@pytest.mark.asyncio
async def test_high_volume_concurrency() -> None:
    """Stress test with 100 concurrent tasks on a capacity of 25."""
    clock = MockClock(1000.0)
    limiter = InMemoryTokenBucketRateLimiter(capacity=25, refill_rate=1e-6, time_func=clock)

    tasks = [limiter.check_rate_limit("stress_key") for _ in range(100)]
    results = await asyncio.gather(*tasks)

    allowed_count = sum(1 for res in results if res[0] is True)
    throttled_count = sum(1 for res in results if res[0] is False)

    assert allowed_count == 25
    assert throttled_count == 75


# =============================================================================
# Key Partition Isolation
# =============================================================================


@pytest.mark.asyncio
async def test_key_isolation() -> None:
    """Exhausting quota for one key has no impact on other keys."""
    clock = MockClock(300.0)
    limiter = InMemoryTokenBucketRateLimiter(capacity=2, refill_rate=1.0, time_func=clock)

    # Exhaust user_a
    await limiter.check_rate_limit("user_a")
    await limiter.check_rate_limit("user_a")
    allowed_a, _, _ = await limiter.check_rate_limit("user_a")
    assert allowed_a is False

    # user_b should have full capacity
    allowed_b1, remaining_b1, _ = await limiter.check_rate_limit("user_b")
    assert allowed_b1 is True
    assert remaining_b1 == 1

    allowed_b2, remaining_b2, _ = await limiter.check_rate_limit("user_b")
    assert allowed_b2 is True
    assert remaining_b2 == 0


@pytest.mark.asyncio
async def test_concurrent_multi_key_isolation() -> None:
    """Multiple distinct keys evaluated concurrently maintain independent capacities."""
    clock = MockClock(400.0)
    limiter = InMemoryTokenBucketRateLimiter(capacity=3, refill_rate=1e-6, time_func=clock)

    keys = [f"tenant_{i}" for i in range(5)]
    # Each key receives 10 concurrent requests
    tasks = [limiter.check_rate_limit(k) for k in keys for _ in range(10)]
    results = await asyncio.gather(*tasks)

    # Total requests = 50. Exactly 5 * 3 = 15 allowed, 35 throttled.
    allowed_total = sum(1 for res in results if res[0] is True)
    assert allowed_total == 15


# =============================================================================
# Variable Cost & Reset Management
# =============================================================================


@pytest.mark.asyncio
async def test_variable_token_cost() -> None:
    """Requests can consume more than 1 token per check."""
    clock = MockClock(50.0)
    limiter = InMemoryTokenBucketRateLimiter(capacity=10, refill_rate=1.0, time_func=clock)

    # Request costing 4 tokens
    allowed1, remaining1, _ = await limiter.check_rate_limit("cost_key", cost=4)
    assert allowed1 is True
    # 6 tokens remain, accommodating 6 // 4 = 1 more request of cost 4
    assert remaining1 == 1

    # Request costing 4 tokens
    allowed2, remaining2, _ = await limiter.check_rate_limit("cost_key", cost=4)
    assert allowed2 is True
    # 2 tokens remain, accommodating 2 // 4 = 0 more requests of cost 4
    assert remaining2 == 0

    # 3rd request costing 4 tokens fails (2 < 4)
    allowed3, remaining3, _ = await limiter.check_rate_limit("cost_key", cost=4)
    assert allowed3 is False
    assert remaining3 == 0


@pytest.mark.asyncio
async def test_reset_specific_key_and_all_keys() -> None:
    """Keys can be reset individually or cleared entirely."""
    clock = MockClock(60.0)
    limiter = InMemoryTokenBucketRateLimiter(capacity=2, refill_rate=1.0, time_func=clock)

    # Exhaust both keys
    for _ in range(2):
        await limiter.check_rate_limit("key_1")
        await limiter.check_rate_limit("key_2")

    assert (await limiter.check_rate_limit("key_1"))[0] is False
    assert (await limiter.check_rate_limit("key_2"))[0] is False

    # Reset only key_1
    await limiter.reset("key_1")
    assert (await limiter.check_rate_limit("key_1"))[0] is True
    assert (await limiter.check_rate_limit("key_2"))[0] is False

    # Reset all keys
    await limiter.reset()
    assert len(limiter.get_tracked_keys()) == 0
    assert (await limiter.check_rate_limit("key_2"))[0] is True


# =============================================================================
# Input Validation & Errors
# =============================================================================


def test_invalid_capacity_raises_validation_error() -> None:
    """Non-positive or non-numeric capacity raises RateLimiterValidationError."""
    for invalid in [0, -1, -5.5, float("nan"), float("inf"), "10", True, False, None]:
        with pytest.raises(RateLimiterValidationError):
            InMemoryTokenBucketRateLimiter(capacity=invalid, refill_rate=1.0)  # type: ignore[arg-type]


def test_invalid_refill_rate_raises_validation_error() -> None:
    """Non-positive or non-numeric refill_rate raises RateLimiterValidationError."""
    for invalid in [0, -0.5, float("nan"), float("inf"), "1.0", True, False, None]:
        with pytest.raises(RateLimiterValidationError):
            InMemoryTokenBucketRateLimiter(capacity=5, refill_rate=invalid)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_invalid_key_raises_validation_error() -> None:
    """Empty or non-string partition key raises RateLimiterValidationError."""
    limiter = InMemoryTokenBucketRateLimiter(capacity=5, refill_rate=1.0)
    for invalid in ["", "   ", "\t\n", None, 123, ["key"]]:
        with pytest.raises(RateLimiterValidationError):
            await limiter.check_rate_limit(invalid)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_invalid_cost_raises_validation_error() -> None:
    """Non-positive or non-numeric cost raises RateLimiterValidationError."""
    limiter = InMemoryTokenBucketRateLimiter(capacity=5, refill_rate=1.0)
    for invalid in [0, -1, float("nan"), float("inf"), "2", True, False, None]:
        with pytest.raises(RateLimiterValidationError):
            await limiter.check_rate_limit("key", cost=invalid)  # type: ignore[arg-type]
