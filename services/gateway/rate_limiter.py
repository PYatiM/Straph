"""Asynchronous token bucket and Redis fixed-window rate limiters for API gateway.

Provides:
- RateLimiterProtocol: Runtime-checkable protocol for asynchronous rate limiters.
- InMemoryTokenBucketRateLimiter: In-memory token bucket implementation using per-key
  asyncio.Lock to ensure thread-safe and race-free concurrent rate limiting.
- RedisFixedWindowRateLimiter: Distributed fixed-window rate limiter utilizing Redis
  and an atomic Lua script with graceful fail-open fallback on connection errors.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from collections.abc import Callable
from typing import Any, Protocol, runtime_checkable

from redis.exceptions import RedisError

__all__ = [
    "InMemoryTokenBucketRateLimiter",
    "RateLimiterError",
    "RateLimiterProtocol",
    "RateLimiterValidationError",
    "RedisFixedWindowRateLimiter",
]

logger = logging.getLogger(__name__)

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


class RedisFixedWindowRateLimiter:
    """Redis-backed fixed-window rate limiter using an atomic Lua script.

    Increments a window counter in Redis and sets key expiration on the first hit
    atomically via Lua script. Enforces max_requests per window_sec.
    Fails open safely with a logged warning if Redis is unreachable.
    """

    LUA_INCREMENT_SCRIPT: str = (
        "local current = redis.call('INCR', KEYS[1])\n"
        "if tonumber(current) == 1 then\n"
        "    redis.call('EXPIRE', KEYS[1], ARGV[1])\n"
        "end\n"
        "return current\n"
    )

    def __init__(
        self,
        redis_client: Any,
        max_requests: int = 100,
        window_sec: int = 60,
        *,
        key_prefix: str = "rate_limit:",
        time_func: Callable[[], float] | None = None,
    ) -> None:
        """Initialize RedisFixedWindowRateLimiter.

        Args:
            redis_client: Asynchronous Redis client instance (e.g. redis.asyncio.Redis).
            max_requests: Maximum allowed requests within the window. Must be a positive integer.
            window_sec: Window duration in seconds. Must be a positive integer.
            key_prefix: Prefix namespace for keys stored in Redis.
            time_func: Optional callable returning current epoch time in seconds. Defaults to time.time.

        Raises:
            RateLimiterValidationError: If configuration parameters fail validation.
        """
        if redis_client is None:
            raise RateLimiterValidationError("redis_client must not be None")

        self._validate_int(max_requests, "max_requests")
        self._validate_int(window_sec, "window_sec")

        if not isinstance(key_prefix, str):
            raise RateLimiterValidationError("key_prefix must be a string")

        self.redis_client: Any = redis_client
        self.max_requests: int = int(max_requests)
        self.window_sec: int = int(window_sec)
        self.key_prefix: str = key_prefix
        self._time_func: Callable[[], float] = time_func if time_func is not None else time.time

        self._script: Any = None
        if hasattr(self.redis_client, "register_script") and callable(
            self.redis_client.register_script
        ):
            try:
                self._script = self.redis_client.register_script(self.LUA_INCREMENT_SCRIPT)
            except (AttributeError, TypeError, RedisError):
                self._script = None

    # -------------------------------------------------------------------------
    # Public RateLimiterProtocol API
    # -------------------------------------------------------------------------

    async def check_rate_limit(self, key: str) -> tuple[bool, int, float]:
        """Check whether a request for key is allowed within the current fixed window.

        Args:
            key: Partition identifier for the rate limit bucket.

        Returns:
            tuple[bool, int, float]: (allowed, remaining_quota, reset_epoch_sec)
                - allowed: True if request count is <= max_requests, False if throttled.
                  If Redis is unreachable, gracefully fails open and returns True.
                - remaining_quota: Integer remaining requests allowed in this window.
                - reset_epoch_sec: POSIX timestamp when the current window resets.

        Raises:
            RateLimiterValidationError: If key is invalid.
        """
        self._validate_key(key)
        redis_key = f"{self.key_prefix}{key}"
        now = self._time_func()

        try:
            current = await self._execute_lua(redis_key)
            current_count = int(current)
            allowed = current_count <= self.max_requests
            remaining_quota = max(0, self.max_requests - current_count)

            # Query remaining TTL for accurate reset epoch sec
            try:
                ttl = await self.redis_client.ttl(redis_key)
                ttl_val = (
                    float(ttl)
                    if isinstance(ttl, (int, float)) and ttl > 0
                    else float(self.window_sec)
                )
            except (RedisError, OSError, TimeoutError):
                ttl_val = float(self.window_sec)

            reset_epoch_sec = now + ttl_val
            return (allowed, remaining_quota, reset_epoch_sec)

        except (RedisError, OSError, TimeoutError) as exc:
            logger.warning(
                "Redis rate limiter unreachable for key %r: %s. Gracefully failing open.",
                key,
                exc,
            )
            return (True, self.max_requests, now + float(self.window_sec))

    # -------------------------------------------------------------------------
    # State Inspection & Management Helpers
    # -------------------------------------------------------------------------

    async def reset(self, key: str | None = None) -> None:
        """Reset rate limit counter for a specific key or all keys under prefix.

        Args:
            key: Partition key to reset, or None to clear all prefixed keys.
        """
        try:
            if key is not None:
                self._validate_key(key)
                await self.redis_client.delete(f"{self.key_prefix}{key}")
            else:
                pattern = f"{self.key_prefix}*"
                cursor = 0
                while True:
                    cursor, keys = await self.redis_client.scan(
                        cursor=cursor, match=pattern, count=100
                    )
                    if keys:
                        await self.redis_client.delete(*keys)
                    if cursor == 0:
                        break
        except (RedisError, OSError, TimeoutError) as exc:
            logger.warning("Redis reset failed: %s", exc)

    async def get_current_count(self, key: str) -> int:
        """Inspect current hit count in Redis for key without incrementing."""
        self._validate_key(key)
        try:
            val = await self.redis_client.get(f"{self.key_prefix}{key}")
            return int(val) if val is not None else 0
        except (RedisError, OSError, TimeoutError) as exc:
            logger.warning("Redis get_current_count failed for key %r: %s", key, exc)
            return 0

    async def get_ttl(self, key: str) -> int:
        """Inspect remaining TTL in seconds for key."""
        self._validate_key(key)
        try:
            ttl = await self.redis_client.ttl(f"{self.key_prefix}{key}")
            return int(ttl) if isinstance(ttl, (int, float)) else -2
        except (RedisError, OSError, TimeoutError) as exc:
            logger.warning("Redis get_ttl failed for key %r: %s", key, exc)
            return -2

    # -------------------------------------------------------------------------
    # Internal Helpers
    # -------------------------------------------------------------------------

    async def _execute_lua(self, redis_key: str) -> int:
        """Execute the atomic Lua script on the Redis client."""
        if self._script is not None:
            result = await self._script(keys=[redis_key], args=[self.window_sec])
        else:
            result = await self.redis_client.eval(
                self.LUA_INCREMENT_SCRIPT, 1, redis_key, self.window_sec
            )
        return int(result)

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
    def _validate_int(value: Any, name: str) -> None:
        """Validate positive integer parameters."""
        if isinstance(value, bool) or not isinstance(value, int):
            raise RateLimiterValidationError(
                f"{name} must be a positive integer, got {type(value).__name__} ({value!r})"
            )
        if value <= 0:
            raise RateLimiterValidationError(f"{name} must be positive, got {value!r}")
