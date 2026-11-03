"""Unit tests for OpenAPIParser against specification dictionaries."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from services.model_builder.openapi_parser import CandidateOperation, OpenAPIParser

ROOT_DIR = Path(__file__).resolve().parents[2]
TARGET_OPENAPI_PATH = ROOT_DIR / "services" / "evaluation" / "target_app" / "openapi.json"


@pytest.fixture
def target_spec() -> dict[str, Any]:
    with open(TARGET_OPENAPI_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture
def parser() -> OpenAPIParser:
    return OpenAPIParser()


def test_target_app_all_10_endpoints_parsed(
    parser: OpenAPIParser,
    target_spec: dict[str, Any],
) -> None:
    """Verify all 10 endpoints in target_app/openapi.json are parsed into operations."""
    ops = parser.parse_spec(target_spec)
    assert len(ops) == 10

    op_ids = {op.operation_id for op in ops}
    expected_ids = {
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
    assert op_ids == expected_ids


def test_resource_type_inference_target_app(
    parser: OpenAPIParser,
    target_spec: dict[str, Any],
) -> None:
    """Verify resource_type is inferred for parameterized routes and None for non-parameterized."""
    ops = parser.parse_spec(target_spec)
    ops_by_id = {op.operation_id: op for op in ops}

    # 7 parameterized routes under /orders/{order_id}/...
    order_sub_ops = [
        "UpdateAddress",
        "InitiatePayment",
        "FulfillOrder",
        "ShipOrder",
        "DeliverOrder",
        "CancelOrder",
        "RefundOrder",
    ]
    for op_id in order_sub_ops:
        assert ops_by_id[op_id].resource_type == "orders"

    # Non-parameterized routes have no preceding segment before an id parameter
    assert ops_by_id["AddCartItem"].resource_type is None
    assert ops_by_id["CreateOrder"].resource_type is None
    assert ops_by_id["PaymentWebhook"].resource_type is None


def test_workflow_links_extracted_accurately(
    parser: OpenAPIParser,
    target_spec: dict[str, Any],
) -> None:
    """Verify declared links are extracted and operations with no links return empty list."""
    ops = parser.parse_spec(target_spec)
    ops_by_id = {op.operation_id: op for op in ops}

    # CreateOrder has 3 declared links
    create_links = ops_by_id["CreateOrder"].links
    assert len(create_links) == 3
    create_target_ids = ops_by_id["CreateOrder"].link_operation_ids
    assert set(create_target_ids) == {
        "UpdateAddress",
        "InitiatePayment",
        "CancelOrder",
    }

    # InitiatePayment links to PaymentWebhook
    init_links = ops_by_id["InitiatePayment"].link_operation_ids
    assert init_links == ["PaymentWebhook"]

    # PaymentWebhook links to FulfillOrder
    webhook_links = ops_by_id["PaymentWebhook"].link_operation_ids
    assert webhook_links == ["FulfillOrder"]

    # Endpoints with no links have explicit empty list
    assert ops_by_id["AddCartItem"].links == []
    assert ops_by_id["UpdateAddress"].links == []
    assert ops_by_id["RefundOrder"].links == []


def test_infer_resource_type_edge_cases(parser: OpenAPIParser) -> None:
    """Test path segment extraction across varied patterns."""
    assert parser.infer_resource_type("/orders/{id}/ship") == "orders"
    assert parser.infer_resource_type("/api/v1/users/{user_id}/profile") == "users"
    assert parser.infer_resource_type("/{id}") is None
    assert parser.infer_resource_type("/items") is None
    assert parser.infer_resource_type("") is None


def test_security_roles_extraction(parser: OpenAPIParser) -> None:
    """Verify security roles are extracted from operation and spec definitions."""
    spec = {
        "openapi": "3.0.3",
        "paths": {
            "/admin/users/{user_id}": {
                "get": {
                    "operationId": "getUser",
                    "security": [{"BearerAuth": ["admin", "superadmin"]}],
                    "x-roles": ["auditor"],
                    "responses": {"200": {"description": "OK"}},
                }
            }
        },
    }
    ops = parser.parse_spec(spec)
    assert len(ops) == 1
    op: CandidateOperation = ops[0]
    assert op.operation_id == "getUser"
    assert op.resource_type == "users"
    assert "admin" in op.security_roles
    assert "superadmin" in op.security_roles
    assert "auditor" in op.security_roles
    assert op.links == []
