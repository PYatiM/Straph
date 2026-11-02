"""OpenAPI 3.0 export for the Straph evaluation target app.

FastAPI normally produces OpenAPI 3.1. Straph's tooling expects OpenAPI 3.0 with explicit
``links`` that describe the business workflow (create -> address -> payment -> webhook ->
fulfil). This module:

1. builds the spec as 3.0.3 (converting the few 3.1-only constructs FastAPI/pydantic emit),
2. embeds the workflow ``links`` between operations (by ``operationId``), and
3. can write the result to ``openapi.json`` next to this file.

Regenerate the file after changing any route::

    python -m services.evaluation.target_app.openapi_export
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi.openapi.utils import get_openapi

if TYPE_CHECKING:
    from fastapi import FastAPI

OPENAPI_VERSION = "3.0.3"
DEFAULT_SPEC_PATH = Path(__file__).with_name("openapi.json")

_ORDER_ID_FROM_BODY = "$response.body#/order_id"


def _is_null_schema(schema: Any) -> bool:
    return isinstance(schema, dict) and schema == {"type": "null"}


def _downgrade(node: Any) -> Any:
    """Rewrite OpenAPI 3.1 / JSON Schema 2020-12 constructs so the result is valid 3.0."""
    if isinstance(node, list):
        return [_downgrade(item) for item in node]
    if not isinstance(node, dict):
        return node

    out: dict[str, Any] = {key: _downgrade(value) for key, value in node.items()}

    any_of = out.get("anyOf")
    if isinstance(any_of, list) and any(_is_null_schema(option) for option in any_of):
        remaining = [option for option in any_of if not _is_null_schema(option)]
        del out["anyOf"]
        out["nullable"] = True
        if len(remaining) == 1 and "$ref" in remaining[0]:
            out["allOf"] = remaining  # 3.0 ignores siblings of $ref
        elif len(remaining) == 1:
            for key, value in remaining[0].items():
                out.setdefault(key, value)
        else:
            out["anyOf"] = remaining

    if "const" in out:
        out["enum"] = [out.pop("const")]
    if isinstance(out.get("examples"), list) and out["examples"]:
        out["example"] = out.pop("examples")[0]
    return out


def _operations_by_id(schema: dict[str, Any]) -> dict[str, dict[str, Any]]:
    operations: dict[str, dict[str, Any]] = {}
    for path_item in schema.get("paths", {}).values():
        for operation in path_item.values():
            if isinstance(operation, dict) and "operationId" in operation:
                operations[operation["operationId"]] = operation
    return operations


def _id_link(target: str, description: str) -> dict[str, Any]:
    """Link that passes the response's ``order_id`` as the target's ``order_id`` path param."""
    return {
        "operationId": target,
        "parameters": {"order_id": _ORDER_ID_FROM_BODY},
        "description": description,
    }


# (source operationId, response status) -> {link name: link object}
WORKFLOW_LINKS: dict[tuple[str, str], dict[str, dict[str, Any]]] = {
    ("CreateOrder", "201"): {
        "UpdateAddress": _id_link("UpdateAddress", "Set the shipping address of the new order."),
        "InitiatePayment": _id_link("InitiatePayment", "Start payment for the new order."),
        "CancelOrder": _id_link("CancelOrder", "Cancel the new order."),
    },
    ("InitiatePayment", "200"): {
        "PaymentWebhook": {
            "operationId": "PaymentWebhook",
            "requestBody": {"order_id": _ORDER_ID_FROM_BODY},
            "description": "The payment gateway confirms payment for this order.",
        },
    },
    ("PaymentWebhook", "200"): {
        "FulfillOrder": _id_link("FulfillOrder", "The warehouse queues the paid order."),
    },
}


def _add_workflow_links(schema: dict[str, Any]) -> None:
    operations = _operations_by_id(schema)
    for (source, status_code), links in WORKFLOW_LINKS.items():
        if source not in operations:
            raise ValueError(f"cannot add links: unknown source operation {source!r}")
        for name, link in links.items():
            if link["operationId"] not in operations:
                raise ValueError(f"link {name!r} points at unknown operation")
        response = operations[source]["responses"][status_code]
        response["links"] = {name: dict(link) for name, link in links.items()}


def build_openapi(app: FastAPI) -> dict[str, Any]:
    """Build the OpenAPI 3.0 document (with workflow links) for ``app``."""
    raw = get_openapi(
        title=app.title,
        version=app.version,
        openapi_version=OPENAPI_VERSION,
        description=app.description,
        routes=app.routes,
    )
    schema: dict[str, Any] = _downgrade(raw)
    _add_workflow_links(schema)
    return schema


def install_openapi(app: FastAPI) -> None:
    """Make ``app.openapi()`` and ``GET /openapi.json`` serve the 3.0 document with links."""

    def custom_openapi() -> dict[str, Any]:
        if app.openapi_schema is None:
            app.openapi_schema = build_openapi(app)
        return app.openapi_schema

    app.openapi = custom_openapi  # type: ignore[method-assign]


def export_openapi(app: FastAPI, path: Path = DEFAULT_SPEC_PATH) -> Path:
    """Write the app's OpenAPI document to ``path`` as pretty JSON and return the path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(app.openapi(), indent=2) + "\n", encoding="utf-8")
    return path


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export the target app OpenAPI spec.")
    parser.add_argument("--output", type=Path, default=DEFAULT_SPEC_PATH)
    args = parser.parse_args(argv)

    from services.evaluation.target_app.app import create_app

    written = export_openapi(create_app(), args.output)
    print(f"wrote {written}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
