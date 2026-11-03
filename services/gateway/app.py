"""FastAPI Gateway application integrating rate limiting, event interception, and reverse proxy."""

from __future__ import annotations

import logging
import os
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from services.gateway.config import GatewaySettings, get_settings
from services.gateway.middleware import EventInterceptorMiddleware
from services.gateway.producer import (
    EventProducerProtocol,
    InMemoryEventProducer,
    KafkaEventProducer,
)
from services.gateway.rate_limit_middleware import RateLimitMiddleware
from services.gateway.rate_limiter import (
    InMemoryTokenBucketRateLimiter,
    RateLimiterProtocol,
    RedisFixedWindowRateLimiter,
)

log = logging.getLogger("straph.gateway.app")

DEFAULT_TARGET_APP_URL = "http://localhost:8001"
HOP_BY_HOP_HEADERS = frozenset(
    {"content-length", "content-encoding", "transfer-encoding", "connection", "keep-alive"}
)
UUID_OR_ID_SEGMENT = re.compile(r"^[0-9a-fA-F\-]{4,}$|^[0-9]+$")


class ProxyRoute:
    """Mock route object exposing normalized path template to EventInterceptorMiddleware."""

    def __init__(self, path: str) -> None:
        self.path = path


def normalize_route_template(path: str) -> str:
    """Normalize concrete path with resource ID into standard model route template.

    For example:
        /orders/1042/payment -> /orders/{id}/payment
        /orders/1042 -> /orders/{id}
        /cart/items -> /cart/items
    """
    clean_path = path.split("?", 1)[0].split("#", 1)[0]
    segments = [s for s in clean_path.split("/") if s]
    if not segments:
        return "/"

    # In Straph workflow, /orders/<id>/... uses {id} in reference models
    if (
        segments[0] == "orders"
        and len(segments) >= 2
        and (UUID_OR_ID_SEGMENT.match(segments[1]) or segments[1] != "items")
    ):
        segments[1] = "{id}"
        return "/" + "/".join(segments)

    return "/" + "/".join(segments)


def create_gateway_app(
    *,
    target_app_url: str | None = None,
    producer: EventProducerProtocol | None = None,
    rate_limiter: RateLimiterProtocol | None = None,
    rate_limit: int | None = None,
    settings: GatewaySettings | None = None,
    client: httpx.AsyncClient | None = None,
) -> FastAPI:
    """Assemble the core Gateway FastAPI application."""
    gw_settings = settings or get_settings()
    downstream_url = target_app_url or os.getenv("TARGET_APP_URL", DEFAULT_TARGET_APP_URL)

    # Initialize producer if not supplied
    event_producer = producer
    if event_producer is None:
        kafka_bootstrap = os.getenv("KAFKA_BOOTSTRAP_SERVERS")
        use_kafka = os.getenv("USE_KAFKA", "").lower() in ("1", "true")
        if use_kafka and kafka_bootstrap:
            event_producer = KafkaEventProducer(
                bootstrap_servers=kafka_bootstrap,
                topic=gw_settings.events_topic,
            )
        else:
            event_producer = InMemoryEventProducer()

    # Initialize rate limiter if not supplied
    limiter = rate_limiter
    if limiter is None:
        redis_url = os.getenv("REDIS_URL")
        use_redis = os.getenv("USE_REDIS", "").lower() in ("1", "true")
        if use_redis and redis_url:
            limiter = RedisFixedWindowRateLimiter(
                redis_url=redis_url,
                max_requests=int(os.getenv("RATE_LIMIT_MAX_REQUESTS", "100")),
                window_seconds=int(os.getenv("RATE_LIMIT_WINDOW_SECONDS", "60")),
            )
        else:
            capacity = int(os.getenv("RATE_LIMIT_CAPACITY", "100"))
            refill = float(os.getenv("RATE_LIMIT_REFILL_RATE", "100.0"))
            limiter = InMemoryTokenBucketRateLimiter(
                capacity=capacity,
                refill_rate=refill,
            )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if not hasattr(app.state, "client") or app.state.client is None:
            app.state.client = httpx.AsyncClient(base_url=downstream_url)
            app.state.owns_client = True
        else:
            app.state.owns_client = False
        yield
        if getattr(app.state, "owns_client", False):
            await app.state.client.aclose()
        if hasattr(app.state.producer, "close"):
            await app.state.producer.close()

    app = FastAPI(title="Straph API Gateway", lifespan=lifespan)
    app.state.target_app_url = downstream_url
    app.state.producer = event_producer
    app.state.limiter = limiter
    if client is not None:
        app.state.client = client

    # Middleware order:
    # 1. EventInterceptorMiddleware wraps the handler
    # 2. RateLimitMiddleware is added last so it evaluates first on incoming requests
    app.add_middleware(
        EventInterceptorMiddleware,
        producer=event_producer,
        settings=gw_settings,
    )
    app.add_middleware(
        RateLimitMiddleware,
        limiter=limiter,
        limit=rate_limit,
    )

    @app.get("/health", include_in_schema=False)
    async def health(request: Request) -> JSONResponse:
        downstream_status = "healthy"
        target_client: httpx.AsyncClient = request.app.state.client
        health_url = f"{str(request.app.state.target_app_url).rstrip('/')}/health"
        try:
            resp = await target_client.get(health_url, timeout=2.0)
            if resp.status_code != 200:
                downstream_status = f"unhealthy ({resp.status_code})"
        except (httpx.RequestError, TimeoutError, OSError):
            downstream_status = "unavailable"

        return JSONResponse(
            content={
                "status": "healthy" if downstream_status == "healthy" else "degraded",
                "gateway": "running",
                "downstream": downstream_status,
                "target_app_url": str(request.app.state.target_app_url),
            },
            status_code=200,
        )

    async def proxy_request(request: Request, path: str = "") -> Response:
        target_client: httpx.AsyncClient = request.app.state.client
        url = f"{str(request.app.state.target_app_url).rstrip('/')}/{path.lstrip('/')}"
        if request.url.query:
            url = f"{url}?{request.url.query}"

        # Inject normalized route template for event interception
        norm_template = normalize_route_template(request.url.path)
        request.scope["route"] = ProxyRoute(norm_template)

        forward_headers = dict(request.headers)
        forward_headers.pop("host", None)
        forward_headers.pop("content-length", None)

        content = await request.body()
        try:
            downstream_resp = await target_client.request(
                method=request.method,
                url=url,
                headers=forward_headers,
                content=content,
                follow_redirects=False,
            )
        except httpx.RequestError as exc:
            log.warning("Downstream forward error: %s", exc)
            return JSONResponse(
                {"detail": f"Downstream service unavailable: {exc!s}"},
                status_code=502,
            )

        resp_headers = dict(downstream_resp.headers)
        for h in HOP_BY_HOP_HEADERS:
            resp_headers.pop(h, None)

        return Response(
            content=downstream_resp.content,
            status_code=downstream_resp.status_code,
            headers=resp_headers,
            media_type=downstream_resp.headers.get("content-type"),
        )

    app.api_route(
        "/{path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
    )(proxy_request)
    app.api_route(
        "",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
    )(proxy_request)

    return app


app = create_gateway_app()
