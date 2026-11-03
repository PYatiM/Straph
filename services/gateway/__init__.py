"""Gateway service for API ingestion, rate limiting, and event tracking."""

from __future__ import annotations

from services.gateway.app import create_gateway_app
from services.gateway.consumer import AsyncKafkaConsumer
from services.gateway.rate_limit_middleware import (
    RateLimitMiddleware,
    extract_caller_sub,
    extract_client_ip,
    extract_rate_limit_key,
)
from services.gateway.rate_limiter import (
    InMemoryTokenBucketRateLimiter,
    RateLimiterError,
    RateLimiterProtocol,
    RateLimiterValidationError,
    RedisFixedWindowRateLimiter,
)

__all__ = [
    "AsyncKafkaConsumer",
    "InMemoryTokenBucketRateLimiter",
    "RateLimitMiddleware",
    "RateLimiterError",
    "RateLimiterProtocol",
    "RateLimiterValidationError",
    "RedisFixedWindowRateLimiter",
    "create_gateway_app",
    "extract_caller_sub",
    "extract_client_ip",
    "extract_rate_limit_key",
]
