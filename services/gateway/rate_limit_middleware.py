"""ASGI middleware for API gateway rate limiting.

Provides:
- RateLimitMiddleware: BaseHTTPMiddleware implementation enforcing per-caller/per-IP
  rate limits, returning HTTP 429 Too Many Requests with Retry-After on violations,
  and injecting X-RateLimit-* headers into responses.
- extract_caller_sub: Helper extracting subject identifier from JWT or request context.
- extract_client_ip: Helper extracting client IP address from proxy headers or connection host.
- extract_rate_limit_key: Helper combining caller_sub and client IP into partition key.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
import math
import time
from collections.abc import Callable

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

from services.gateway.rate_limiter import RateLimiterProtocol

__all__ = [
    "RateLimitMiddleware",
    "extract_caller_sub",
    "extract_client_ip",
    "extract_rate_limit_key",
]

logger = logging.getLogger(__name__)


def _decode_jwt_sub(token: str) -> str | None:
    """Decode unverified JWT claims and return 'sub' if present."""
    parts = token.split(".")
    if len(parts) < 2 or not parts[1]:
        return None
    segment = parts[1]
    try:
        padded = segment + "=" * (-len(segment) % 4)
        raw = base64.urlsafe_b64decode(padded)
        claims = json.loads(raw)
        if isinstance(claims, dict):
            sub = claims.get("sub")
            if isinstance(sub, str) and sub.strip():
                return sub.strip()
    except (binascii.Error, ValueError, UnicodeDecodeError):
        return None
    return None


def extract_caller_sub(request: Request) -> str:
    """Extract caller subject identifier from request state, headers, or Bearer JWT.

    Args:
        request: Incoming HTTP request.

    Returns:
        String subject identifier, or 'anonymous' if unauthenticated.
    """
    # 1. Check request.state (e.g. set by upstream auth middleware)
    if hasattr(request, "state"):
        for attr in ("sub", "caller_sub", "user_id", "user"):
            val = getattr(request.state, attr, None)
            if isinstance(val, str) and val.strip():
                return val.strip()

    # 2. Check explicit caller identity headers
    for header in ("x-caller-sub", "x-user-id", "x-subject"):
        val = request.headers.get(header)
        if isinstance(val, str) and val.strip():
            return val.strip()

    # 3. Check Authorization header for Bearer JWT
    auth = request.headers.get("authorization")
    if isinstance(auth, str):
        scheme, _, token = auth.strip().partition(" ")
        if scheme.lower() == "bearer" and token.strip():
            sub = _decode_jwt_sub(token.strip())
            if sub is not None:
                return sub

    return "anonymous"


def extract_client_ip(request: Request) -> str:
    """Extract client IP address from proxy headers or connection host.

    Args:
        request: Incoming HTTP request.

    Returns:
        Client IP string, or '127.0.0.1' fallback.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if isinstance(forwarded, str) and forwarded.strip():
        # The leftmost IP in X-Forwarded-For is the originating client IP
        client_ip = forwarded.split(",")[0].strip()
        if client_ip:
            return client_ip

    real_ip = request.headers.get("x-real-ip")
    if isinstance(real_ip, str) and real_ip.strip():
        return real_ip.strip()

    if request.client and request.client.host:
        return request.client.host

    return "127.0.0.1"


def extract_rate_limit_key(request: Request) -> str:
    """Extract composite rate limit partition key combining caller_sub and client IP.

    Args:
        request: Incoming HTTP request.

    Returns:
        Colon-separated string: '{caller_sub}:{client_ip}'.
    """
    caller_sub = extract_caller_sub(request)
    client_ip = extract_client_ip(request)
    return f"{caller_sub}:{client_ip}"


class RateLimitMiddleware(BaseHTTPMiddleware):
    """ASGI middleware enforcing rate limits on incoming HTTP calls.

    For each incoming request:
    1. Extracts rate limit key: combination of caller_sub and client IP.
    2. Calls limiter.check_rate_limit(key).
    3. If not allowed, immediately returns HTTP 429 Too Many Requests with JSON
       body {"detail": "Rate limit exceeded"} and Retry-After header.
    4. If allowed, injects X-RateLimit-Limit, X-RateLimit-Remaining, and
       X-RateLimit-Reset headers into the response.
    """

    def __init__(
        self,
        app: ASGIApp,
        limiter: RateLimiterProtocol,
        limit: int | None = None,
        *,
        key_extractor: Callable[[Request], str] | None = None,
        time_func: Callable[[], float] | None = None,
    ) -> None:
        """Initialize RateLimitMiddleware.

        Args:
            app: ASGI application.
            limiter: RateLimiterProtocol instance (e.g. token bucket or Redis fixed window).
            limit: Optional explicit maximum quota for X-RateLimit-Limit header.
                Inferred from limiter if None.
            key_extractor: Optional callable accepting Request and returning string partition key.
                Defaults to `extract_rate_limit_key`.
            time_func: Optional callable returning current epoch time in seconds.
                Defaults to `time.time`.
        """
        super().__init__(app)
        self.limiter: RateLimiterProtocol = limiter
        self.limit: int | None = limit
        self._key_extractor: Callable[[Request], str] = key_extractor or extract_rate_limit_key
        self._time_func: Callable[[], float] = time_func if time_func is not None else time.time

    def _resolve_limit(self) -> int:
        """Resolve numeric rate limit for response headers."""
        if self.limit is not None:
            return self.limit
        if hasattr(self.limiter, "capacity"):
            return int(self.limiter.capacity)
        if hasattr(self.limiter, "max_requests"):
            return int(self.limiter.max_requests)
        return 0

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        """Process incoming request through the rate limiter.

        Args:
            request: Incoming HTTP request.
            call_next: Endpoint handler yielding the HTTP response.

        Returns:
            HTTP 429 Response if throttled, otherwise the response from call_next with
            rate limit headers injected.
        """
        # 1. Extract rate limit key: combination of caller_sub and client IP
        key = self._key_extractor(request)
        effective_limit = self._resolve_limit()

        # 2. Call limiter.check_rate_limit(key)
        try:
            allowed, remaining_quota, reset_epoch_sec = await self.limiter.check_rate_limit(key)
        except Exception:
            logger.exception("Rate limiter check failed for key %r. Failing open.", key)
            allowed = True
            remaining_quota = effective_limit
            reset_epoch_sec = self._time_func() + 60.0

        # 3. If not allowed, immediately return HTTP 429 with JSON body and Retry-After
        if not allowed:
            now = self._time_func()
            retry_after = max(1, math.ceil(reset_epoch_sec - now))
            headers = {
                "Retry-After": str(retry_after),
                "X-RateLimit-Limit": str(effective_limit),
                "X-RateLimit-Remaining": str(remaining_quota),
                "X-RateLimit-Reset": str(int(reset_epoch_sec)),
            }
            return JSONResponse(
                status_code=429,
                content={"detail": "Rate limit exceeded"},
                headers=headers,
            )

        # 4. If allowed, inject X-RateLimit-* headers into the response
        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(effective_limit)
        response.headers["X-RateLimit-Remaining"] = str(remaining_quota)
        response.headers["X-RateLimit-Reset"] = str(int(reset_epoch_sec))
        return response
