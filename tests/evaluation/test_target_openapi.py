"""Tests for the target app OpenAPI 3.0 export and its workflow links."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from openapi_spec_validator import validate

from services.evaluation.target_app.app import create_app
from services.evaluation.target_app.openapi_export import (
    DEFAULT_SPEC_PATH,
    WORKFLOW_LINKS,
    export_openapi,
    main,
)

EXPECTED_OPERATION_IDS = {
    "AddCartItem",
    "CreateOrder",
    "UpdateAddress",
    "InitiatePayment",
    "PaymentWebhook",
    "FulfillOrder",
    "ShipOrder",
    "DeliverOrder",
    "CancelOrder",
    "RefundOrder",
}


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def spec() -> dict[str, Any]:
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/openapi.json")
    assert resp.status_code == 200
    body: dict[str, Any] = resp.json()
    return body


def _operations(spec: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        op["operationId"]: op
        for item in spec["paths"].values()
        for op in item.values()
        if "operationId" in op
    }


def _resolve(spec: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    if "$ref" in schema:
        name = schema["$ref"].rsplit("/", 1)[-1]
        resolved: dict[str, Any] = spec["components"]["schemas"][name]
        return resolved
    return schema


def _contains(node: Any, predicate: Any) -> bool:
    if predicate(node):
        return True
    if isinstance(node, dict):
        return any(_contains(v, predicate) for v in node.values())
    if isinstance(node, list):
        return any(_contains(v, predicate) for v in node)
    return False


def _json_schema(spec: dict[str, Any], container: dict[str, Any]) -> dict[str, Any]:
    return _resolve(spec, container["content"]["application/json"]["schema"])


def _all_links(spec: dict[str, Any]) -> list[tuple[str, str, str, dict[str, Any]]]:
    found = []
    for op_id, op in _operations(spec).items():
        for status_code, response in op["responses"].items():
            for name, link in response.get("links", {}).items():
                found.append((op_id, status_code, name, link))
    return found


# --- validity -----------------------------------------------------------------------------


@pytest.mark.anyio
async def test_openapi_endpoint_serves_a_3_0_document(spec: dict[str, Any]) -> None:
    assert spec["openapi"].startswith("3.0.")


@pytest.mark.anyio
async def test_document_is_valid_openapi(spec: dict[str, Any]) -> None:
    validate(spec)


@pytest.mark.anyio
async def test_no_openapi_3_1_only_constructs_remain(spec: dict[str, Any]) -> None:
    assert not _contains(spec, lambda n: n == {"type": "null"})
    assert not _contains(spec, lambda n: isinstance(n, dict) and "const" in n)


@pytest.mark.anyio
async def test_every_operation_has_a_unique_expected_id(spec: dict[str, Any]) -> None:
    ids = [op["operationId"] for item in spec["paths"].values() for op in item.values()]
    assert len(ids) == len(set(ids))
    assert set(ids) == EXPECTED_OPERATION_IDS


@pytest.mark.anyio
async def test_response_schemas_are_defined(spec: dict[str, Any]) -> None:
    schemas = spec["components"]["schemas"]
    for name in ("CreateOrderResponse", "PaymentResponse", "OrderStateResponse"):
        assert name in schemas
    assert "order_id" in schemas["CreateOrderResponse"]["properties"]


@pytest.mark.anyio
async def test_document_is_cached_between_calls() -> None:
    app = create_app()
    assert app.openapi() is app.openapi()


# --- links --------------------------------------------------------------------------------


@pytest.mark.anyio
async def test_create_order_links_to_address_payment_and_cancel(spec: dict[str, Any]) -> None:
    links = _operations(spec)["CreateOrder"]["responses"]["201"]["links"]
    assert set(links) == {"UpdateAddress", "InitiatePayment", "CancelOrder"}
    for name, link in links.items():
        assert link["operationId"] == name
        assert link["parameters"] == {"order_id": "$response.body#/order_id"}


@pytest.mark.anyio
async def test_initiate_payment_links_to_webhook(spec: dict[str, Any]) -> None:
    links = _operations(spec)["InitiatePayment"]["responses"]["200"]["links"]
    assert set(links) == {"PaymentWebhook"}
    assert links["PaymentWebhook"]["operationId"] == "PaymentWebhook"
    assert links["PaymentWebhook"]["requestBody"] == {"order_id": "$response.body#/order_id"}


@pytest.mark.anyio
async def test_webhook_links_to_fulfill(spec: dict[str, Any]) -> None:
    links = _operations(spec)["PaymentWebhook"]["responses"]["200"]["links"]
    assert set(links) == {"FulfillOrder"}
    assert links["FulfillOrder"]["operationId"] == "FulfillOrder"
    assert links["FulfillOrder"]["parameters"] == {"order_id": "$response.body#/order_id"}


@pytest.mark.anyio
async def test_only_the_planned_links_exist(spec: dict[str, Any]) -> None:
    found = {(op, status) for op, status, _, _ in _all_links(spec)}
    assert found == set(WORKFLOW_LINKS)


@pytest.mark.anyio
async def test_links_point_at_real_operations_and_parameters(spec: dict[str, Any]) -> None:
    operations = _operations(spec)
    for _, _, name, link in _all_links(spec):
        target = operations[link["operationId"]]
        if "parameters" in link:
            declared = {p["name"] for p in target.get("parameters", [])}
            assert set(link["parameters"]) <= declared, name
        if "requestBody" in link:
            body_schema = _json_schema(spec, target["requestBody"])
            assert set(link["requestBody"]) <= set(body_schema["properties"]), name


@pytest.mark.anyio
async def test_link_expressions_read_fields_the_source_response_has(
    spec: dict[str, Any],
) -> None:
    operations = _operations(spec)
    prefix = "$response.body#/"
    for source, status_code, name, link in _all_links(spec):
        response = operations[source]["responses"][status_code]
        properties = _json_schema(spec, response)["properties"]
        expressions = [*link.get("parameters", {}).values(), *link.get("requestBody", {}).values()]
        for expression in expressions:
            assert expression.startswith(prefix), name
            assert expression[len(prefix) :] in properties, name


# --- export -------------------------------------------------------------------------------


def test_export_writes_the_same_document_the_endpoint_serves(tmp_path: Path) -> None:
    app = create_app()
    written = export_openapi(app, tmp_path / "out" / "openapi.json")
    assert json.loads(written.read_text(encoding="utf-8")) == app.openapi()


def test_cli_main_writes_output_file(tmp_path: Path) -> None:
    target = tmp_path / "spec.json"
    assert main(["--output", str(target)]) == 0
    assert json.loads(target.read_text(encoding="utf-8"))["openapi"].startswith("3.0.")


def test_committed_spec_file_is_up_to_date() -> None:
    """If this fails, run: python -m services.evaluation.target_app.openapi_export"""
    committed = json.loads(DEFAULT_SPEC_PATH.read_text(encoding="utf-8"))
    assert committed == create_app().openapi()


def test_committed_spec_file_is_valid() -> None:
    validate(json.loads(DEFAULT_SPEC_PATH.read_text(encoding="utf-8")))
