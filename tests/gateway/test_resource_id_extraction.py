"""Tests for server-generated resource id extraction from JSON creation responses."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any, TypeVar

import httpx
import pytest
from fastapi import FastAPI, Response
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse

from services.gateway.config import GatewaySettings
from services.gateway.middleware import EventInterceptorMiddleware
from services.gateway.models import APIEventPayload
from services.gateway.producer import InMemoryEventProducer

T = TypeVar("T")


def build_app(producer: InMemoryEventProducer, settings: GatewaySettings | None = None) -> FastAPI:
    app = FastAPI()
    app.add_middleware(EventInterceptorMiddleware, producer=producer, settings=settings)

    @app.post("/orders")
    async def create_order() -> JSONResponse:
        return JSONResponse({"order_id": "1042", "id": "ignored", "total": 5}, status_code=201)

    @app.post("/invoices")
    async def create_invoice() -> JSONResponse:
        return JSONResponse({"id": 9})

    @app.post("/widgets")
    async def create_widget() -> JSONResponse:
        return JSONResponse({"widget_ref": "w-3"}, status_code=201)

    @app.post("/text")
    async def text() -> PlainTextResponse:
        return PlainTextResponse("order_id=1042", status_code=201)

    @app.post("/badjson")
    async def bad_json() -> Response:
        return Response(b'{"order_id": "1', status_code=201, media_type="application/json")

    @app.post("/listjson")
    async def list_json() -> JSONResponse:
        return JSONResponse([{"id": "1"}], status_code=201)

    @app.post("/failed")
    async def failed() -> JSONResponse:
        return JSONResponse({"id": "5"}, status_code=400)

    @app.get("/orders/{oid}")
    async def read(oid: str) -> JSONResponse:
        return JSONResponse({"id": "other"})

    @app.post("/orders/{oid}/payment")
    async def pay(oid: str) -> JSONResponse:
        return JSONResponse({"id": "pay-1"}, status_code=200)

    @app.post("/stream")
    async def stream() -> StreamingResponse:
        async def gen() -> AsyncIterator[bytes]:
            for part in (b'{"id": "s-', b"77", b'", "pad": "x"}'):
                yield part

        return StreamingResponse(gen(), status_code=201, media_type="application/json")

    @app.post("/big")
    async def big() -> StreamingResponse:
        async def gen() -> AsyncIterator[bytes]:
            yield b'{"id": "b-1", "pad": "'
            for _ in range(50):
                yield b"x" * 100
            yield b'"}'

        return StreamingResponse(gen(), status_code=201, media_type="application/json")

    @app.post("/typed")
    async def typed() -> JSONResponse:
        return JSONResponse({"id": True}, status_code=201)

    return app


def run(coro: Callable[[httpx.AsyncClient], Awaitable[T]], app: FastAPI) -> T:
    async def go() -> T:
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await coro(client)

    return asyncio.run(go())


def post(path: str, settings: GatewaySettings | None = None) -> tuple[httpx.Response, Any]:
    producer = InMemoryEventProducer()
    response = run(lambda c: c.post(path), build_app(producer, settings))
    (item,) = producer.events
    return response, item


def test_order_id_stamped_into_event() -> None:
    response, (key, event) = post("/orders")
    assert isinstance(event, APIEventPayload)
    assert event.resource_id == "1042"
    assert key == "orders:1042"
    assert event.status_code == 201
    assert response.json() == {"order_id": "1042", "id": "ignored", "total": 5}


def test_default_mapping_uses_id_and_integer_ids_stringified() -> None:
    response, (key, event) = post("/invoices")
    assert (event.resource_id, key) == ("9", "invoices:9")
    assert response.status_code == 200
    assert response.json() == {"id": 9}


def test_configurable_mapping_per_resource_type() -> None:
    _, (_, unmapped) = post("/widgets")
    assert unmapped.resource_id is None
    settings = GatewaySettings(RESOURCE_ID_FIELDS={"widgets": ("widget_ref",)})
    _, (key, event) = post("/widgets", settings)
    assert (event.resource_id, key) == ("w-3", "widgets:w-3")


def test_mapping_from_environment_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RESOURCE_ID_FIELDS", '{"widgets": ["widget_ref"], "default": ["id"]}')
    _, (_, event) = post("/widgets", GatewaySettings())
    assert event.resource_id == "w-3"


@pytest.mark.parametrize("path", ["/text", "/badjson", "/listjson", "/typed"])
def test_non_json_or_unusable_body_leaves_resource_id_none(path: str) -> None:
    response, (_, event) = post(path)
    assert event.resource_id is None
    assert response.status_code == 201


def test_non_json_body_reaches_client_intact() -> None:
    response, _ = post("/text")
    assert response.text == "order_id=1042"
    response, _ = post("/badjson")
    assert response.content == b'{"order_id": "1'


def test_failed_creation_not_inspected() -> None:
    response, (_, event) = post("/failed")
    assert event.resource_id is None
    assert response.json() == {"id": "5"}


def test_get_and_existing_path_id_not_overridden() -> None:
    producer = InMemoryEventProducer()
    run(lambda c: c.get("/orders/7"), build_app(producer))
    run(lambda c: c.post("/orders/7/payment"), build_app(producer))
    assert [e.resource_id for _, e in producer.events] == ["7", "7"]


def test_streaming_body_reassembled_intact() -> None:
    response, (_, event) = post("/stream")
    assert event.resource_id == "s-77"
    assert response.content == b'{"id": "s-77", "pad": "x"}'


def test_oversized_body_forwarded_in_full_without_extraction() -> None:
    settings = GatewaySettings(RESOURCE_ID_MAX_BODY_BYTES=100)
    response, (_, event) = post("/big", settings)
    assert event.resource_id is None
    body = json.loads(response.content)
    assert body["id"] == "b-1"
    assert body["pad"] == "x" * 5000


def test_body_contents_not_logged(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.DEBUG):
        post("/orders")
    assert "1042" not in caplog.text
    assert "total" not in caplog.text
