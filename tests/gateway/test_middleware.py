"""Tests for EventInterceptorMiddleware using httpx.AsyncClient and InMemoryEventProducer."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypeVar

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from jsonschema import Draft7Validator

from services.gateway.config import GatewaySettings
from services.gateway.middleware import EventInterceptorMiddleware, format_timestamp
from services.gateway.models import APIEventPayload
from services.gateway.producer import InMemoryEventProducer

T = TypeVar("T")
SCHEMA = json.loads(
    (Path(__file__).resolve().parents[2] / "contracts" / "event.schema.json").read_text("utf-8")
)
VALIDATOR = Draft7Validator(SCHEMA)
TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$")


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def token(**claims: Any) -> str:
    return f"{b64(b'{}')}.{b64(json.dumps(claims).encode())}.sig"


class ExplodingProducer:
    async def produce_event(self, key: str, event: APIEventPayload) -> bool:
        raise RuntimeError("kafka down")

    async def close(self) -> None:
        return None


def build_app(producer: Any, settings: GatewaySettings | None = None) -> FastAPI:
    app = FastAPI()
    app.add_middleware(EventInterceptorMiddleware, producer=producer, settings=settings)

    @app.get("/orders/{order_id}")
    async def get_order(order_id: str) -> dict[str, str]:
        return {"id": order_id}

    @app.post("/orders/{order_id}/payment", status_code=202)
    async def pay(order_id: str) -> dict[str, str]:
        return {"status": "accepted"}

    @app.post("/orders")
    async def create() -> JSONResponse:
        return JSONResponse({"id": "77"}, status_code=201, headers={"Location": "/orders/77"})

    @app.post("/login")
    async def login() -> dict[str, str]:
        return {"access_token": "SECRET-RESPONSE-TOKEN"}

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("handler crashed")

    @app.get("/forbidden")
    async def forbidden() -> None:
        raise HTTPException(status_code=403)

    return app


def run(coro: Callable[[httpx.AsyncClient], Awaitable[T]], app: FastAPI) -> T:
    async def go() -> T:
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await coro(client)

    return asyncio.run(go())


def events(producer: InMemoryEventProducer) -> list[APIEventPayload]:
    return [e for _, e in producer.events]


def test_event_emitted_for_every_response_and_schema_valid() -> None:
    producer = InMemoryEventProducer()

    async def calls(c: httpx.AsyncClient) -> list[int]:
        responses = [
            await c.get("/orders/1042"),
            await c.post("/orders/1042/payment"),
            await c.get("/forbidden"),
            await c.get("/missing"),
            await c.get("/orders/1042", params={"x": "1"}),
        ]
        return [r.status_code for r in responses]

    codes = run(calls, build_app(producer))
    assert codes == [200, 202, 403, 404, 200]
    assert [e.status_code for e in events(producer)] == codes
    for _, e in producer.events:
        assert list(VALIDATOR.iter_errors(e.to_contract_dict())) == []


def test_keys_and_route_template() -> None:
    producer = InMemoryEventProducer()

    async def calls(c: httpx.AsyncClient) -> None:
        await c.post("/orders/1042/payment")

    run(calls, build_app(producer))
    ((key, event),) = producer.events
    assert key == "orders:1042"
    assert event.operation.method == "POST"
    assert event.operation.path_template == "/orders/{order_id}/payment"
    assert event.resource_id == "1042"


def test_timestamps_ordered_utc_and_in_window() -> None:
    producer = InMemoryEventProducer()
    before = datetime.now(UTC)
    run(lambda c: c.get("/orders/1"), build_app(producer))
    after = datetime.now(UTC)
    (event,) = events(producer)
    assert TS_RE.match(event.ts_received) and TS_RE.match(event.ts_completed)
    received = datetime.fromisoformat(event.ts_received)
    completed = datetime.fromisoformat(event.ts_completed)
    assert received.utcoffset() is not None and received.utcoffset().total_seconds() == 0
    assert before <= received <= completed <= after


def test_format_timestamp_normalizes_to_utc() -> None:
    from datetime import timedelta, timezone

    local = datetime(2026, 10, 13, 14, 30, 0, 120, tzinfo=timezone(timedelta(hours=5, minutes=30)))
    assert format_timestamp(local) == "2026-10-13T09:00:00.000120Z"


def test_session_idempotency_and_subject_captured() -> None:
    producer = InMemoryEventProducer()
    headers = {
        "X-Session-Id": "s-a1",
        "X-Idempotency-Key": "idem-9",
        "Authorization": f"Bearer {token(sub='user-A', iss='https://idp', role='customer', jti='tok-1')}",
    }
    run(lambda c: c.post("/orders/5/payment", headers=headers), build_app(producer))
    (e,) = events(producer)
    assert (e.session_id, e.idempotency_key, e.token_id) == ("s-a1", "idem-9", "tok-1")
    assert (e.subject.sub, e.subject.issuer, e.subject.role) == (
        "user-A",
        "https://idp",
        "customer",
    )


def test_custom_idempotency_header_and_session_id_header() -> None:
    producer = InMemoryEventProducer()
    settings = GatewaySettings(IDEMPOTENCY_HEADER="X-Key")
    headers = {"X-Key": "k1", "session_id": "s-2", "X-Idempotency-Key": "ignored"}
    run(lambda c: c.get("/orders/1", headers=headers), build_app(producer, settings))
    (e,) = events(producer)
    assert (e.idempotency_key, e.session_id) == ("k1", "s-2")


def test_anonymous_and_absent_headers() -> None:
    producer = InMemoryEventProducer()
    run(lambda c: c.get("/orders/1"), build_app(producer))
    (e,) = events(producer)
    assert e.subject.sub == "anonymous"
    assert e.idempotency_key is None
    assert e.session_id == ""


def test_created_resource_id_from_location_header() -> None:
    producer = InMemoryEventProducer()
    run(lambda c: c.post("/orders"), build_app(producer))
    ((key, e),) = producer.events
    assert e.status_code == 201
    assert e.resource_id == "77"
    assert key == "orders:77"


def test_handler_exception_emits_500_event() -> None:
    producer = InMemoryEventProducer()
    response = run(lambda c: c.get("/boom"), build_app(producer))
    assert response.status_code == 500
    (e,) = events(producer)
    assert e.status_code == 500
    assert e.resource_type == "boom"


def test_bodies_and_header_values_never_serialized_or_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    producer = InMemoryEventProducer()
    secret_req = {"username": "bob", "password": "SECRET-PASSWORD"}
    jwt = token(sub="u", iss="i", role="r")
    with caplog.at_level(logging.DEBUG):
        run(
            lambda c: c.post("/login", json=secret_req, headers={"Authorization": f"Bearer {jwt}"}),
            build_app(producer),
        )
    dumped = json.dumps([e.to_contract_dict() for e in events(producer)])
    for secret in ("SECRET-PASSWORD", "SECRET-RESPONSE-TOKEN", jwt):
        assert secret not in dumped
        assert secret not in caplog.text


def test_disabled_emits_nothing() -> None:
    producer = InMemoryEventProducer()
    settings = GatewaySettings(GATEWAY_ENABLED=False)
    response = run(lambda c: c.get("/orders/1"), build_app(producer, settings))
    assert response.status_code == 200
    assert producer.events == []


def test_producer_failure_does_not_fail_response(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.ERROR, logger="straph.gateway.middleware"):
        response = run(lambda c: c.get("/orders/1"), build_app(ExplodingProducer()))
    assert response.status_code == 200
    assert response.json() == {"id": "1"}
    assert "failed to emit" in caplog.text
