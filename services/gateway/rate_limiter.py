"""Asynchronous token bucket rate limiter for API gateway.

Provides:
- RateLimiterProtocol: Runtime-checkable protocol for asynchronous rate limiters.
- InMemoryTokenBucketRateLimiter: In-memory token bucket implementation using per-key
  asyncio.Lock to ensure thread-safe and race-free concurrent rate limiting.
"""

from __future__ import annotations

import asyncio
import math
import time
from collections.abc import Callable
from typing import Any, Protocol, runtime_checkable

__all__ = [
    "InMemoryTokenBucketRateLimiter",
    "RateLimiterError",
    "RateLimiterProtocol",
    "RateLimiterValidationError",
]

_FLOAT_EPSILON: float = 1e-9


class RateLimiterError(Exception):
    """Base exception for all rate limiter errors."""


class RateLimiterValidationError(RateLimiterError, ValueError):
    """Raised when rate limiter configuration or inputs fail validation."""


@runtime_checkable
class RateLimiterProtocol(Protocol):
    """Protocol for asynchronous rate limiters."""

    async def check_rate_limit(self, key: str) -> tuple[bool, int, float]:
        """Check whether a request for the given key is allowed under rate limits.

        Args:
            key: Unique identifier for the rate limit partition (e.g. client ID, IP, user ID).

        Returns:
            tuple[bool, int, float]: (allowed, remaining_quota, reset_epoch_sec)
                - allowed: True if request is within quota and permitted; False if throttled.
                - remaining_quota: Non-negative integer representing remaining requests allowed.
                - reset_epoch_sec: POSIX timestamp (epoch seconds) when quota fully replenishes.
        """
        ...


class _TokenBucket:
    """Internal state container for a single key's token bucket."""

    __slots__ = ("last_refill", "lock", "tokens")

    def __init__(self, capacity: float, initial_time: float) -> None:
        self.tokens: float = float(capacity)
        self.last_refill: float = float(initial_time)
        self.lock: asyncio.Lock = asyncio.Lock()


class InMemoryTokenBucketRateLimiter:
    """In-memory token bucket rate limiter with per-key concurrency locking.

    Enforces configurable capacity (maximum burst) and continuous refill rate (tokens/sec).
    Uses an independent `asyncio.Lock` per key to prevent race conditions during
    concurrent asynchronous checks without blocking unrelated keys.
    """

    def __init__(
        self,
        capacity: float = 10,
        refill_rate: float = 1.0,
        *,
        time_func: Callable[[], float] | None = None,
    ) -> None:
        """Initialize InMemoryTokenBucketRateLimiter.

        Args:
            capacity: Maximum token capacity (burst allowance). Must be a positive number.
            refill_rate: Number of tokens added per second. Must be a positive number.
            time_func: Optional callable returning current epoch time in seconds. Defaults
                to `time.time`. Can be overridden for deterministic time control in tests.

        Raises:
            RateLimiterValidationError: If capacity or refill_rate are invalid.
        """
        self._validate_number(capacity, "capacity")
        self._validate_number(refill_rate, "refill_rate")

        self.capacity: float = float(capacity)
        self.refill_rate: float = float(refill_rate)
        self._time_func: Callable[[], float] = time_func if time_func is not None else time.time

        self._buckets: dict[str, _TokenBucket] = {}
        self._master_lock: asyncio.Lock = asyncio.Lock()

    # -------------------------------------------------------------------------
    # Public RateLimiterProtocol API
    # -------------------------------------------------------------------------

    async def check_rate_limit(self, key: str, cost: float = 1.0) -> tuple[bool, int, float]:
        """Check whether a request for key is allowed, consuming tokens on success.

        Args:
            key: Partition identifier for the rate limit bucket.
            cost: Number of tokens to consume for this request. Defaults to 1.0.

        Returns:
            tuple[bool, int, float]: (allowed, remaining_quota, reset_epoch_sec)
                - allowed: True if token was acquired, False if throttled.
                - remaining_quota: Integer count of remaining requests that can be made.
                - reset_epoch_sec: Epoch timestamp when the bucket will be completely full.

        Raises:
            RateLimiterValidationError: If key or cost are invalid.
        """
        self._validate_key(key)
        self._validate_number(cost, "cost")
        num_cost = float(cost)

        bucket = await self._get_or_create_bucket(key)
        async with bucket.lock:
            now = self._time_func()

            # 1. Refill tokens based on elapsed wall-clock time
            elapsed = max(0.0, now - bucket.last_refill)
            if elapsed > 0.0:
                bucket.tokens = min(self.capacity, bucket.tokens + elapsed * self.refill_rate)
                bucket.last_refill = now

            if abs(self.capacity - bucket.tokens) < _FLOAT_EPSILON:
                bucket.tokens = self.capacity

            # 2. Check token sufficiency
            if bucket.tokens >= num_cost:
                allowed = True
                bucket.tokens -= num_cost
                if abs(bucket.tokens) < _FLOAT_EPSILON:
                    bucket.tokens = 0.0
            else:
                allowed = False

            # 3. Calculate remaining quota and reset epoch timestamp
            remaining_quota = max(0, int(bucket.tokens // num_cost)) if num_cost > 0 else 0
            if bucket.tokens < self.capacity and self.refill_rate > 0.0:
                deficit = self.capacity - bucket.tokens
                reset_epoch_sec = now + (deficit / self.refill_rate)
            else:
                reset_epoch_sec = now

            return (allowed, remaining_quota, reset_epoch_sec)

    # -------------------------------------------------------------------------
    # State Inspection & Management Helpers
    # -------------------------------------------------------------------------

    async def get_remaining_tokens(self, key: str) -> float:
        """Inspect current token count for key after applying refills up to current time.

        Args:
            key: Partition identifier.

        Returns:
            Float representing remaining tokens in the bucket.
        """
        self._validate_key(key)
        bucket = await self._get_or_create_bucket(key)
        async with bucket.lock:
            now = self._time_func()
            elapsed = max(0.0, now - bucket.last_refill)
            if elapsed > 0.0:
                bucket.tokens = min(self.capacity, bucket.tokens + elapsed * self.refill_rate)
                bucket.last_refill = now
            if abs(self.capacity - bucket.tokens) < _FLOAT_EPSILON:
                bucket.tokens = self.capacity
            return bucket.tokens

    async def reset(self, key: str | None = None) -> None:
        """Reset rate limit bucket for a specific key or clear all keys.

        Args:
            key: Target partition key to reset to full capacity, or None to clear all.
        """
        if key is None:
            async with self._master_lock:
                self._buckets.clear()
            return

        self._validate_key(key)
        async with self._master_lock:
            bucket = self._buckets.get(key)

        if bucket is not None:
            async with bucket.lock:
                bucket.tokens = self.capacity
                bucket.last_refill = self._time_func()

    def get_tracked_keys(self) -> list[str]:
        """Return a snapshot list of currently tracked keys."""
        return list(self._buckets.keys())

    # -------------------------------------------------------------------------
    # Internal Helpers
    # -------------------------------------------------------------------------

    async def _get_or_create_bucket(self, key: str) -> _TokenBucket:
        """Fetch existing bucket for key or create a new one under master lock."""
        bucket = self._buckets.get(key)
        if bucket is not None:
            return bucket

        async with self._master_lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                bucket = _TokenBucket(self.capacity, self._time_func())
                self._buckets[key] = bucket
            return bucket

    @staticmethod
    def _validate_key(key: str) -> None:
        """Validate partition key string."""
        if not isinstance(key, str):
            raise RateLimiterValidationError(
                f"Rate limit key must be a string, got {type(key).__name__}"
            )
        if not key.strip():
            raise RateLimiterValidationError("Rate limit key must not be empty or whitespace only")

    @staticmethod
    def _validate_number(value: Any, name: str) -> None:
        """Validate numeric configuration parameters."""
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise RateLimiterValidationError(
                f"{name} must be a positive number, got {type(value).__name__} ({value!r})"
            )
        if value <= 0 or math.isnan(value) or math.isinf(value):
            raise RateLimiterValidationError(f"{name} must be positive and finite, got {value!r}")
