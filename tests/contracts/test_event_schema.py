"""Contract tests for contracts/event.schema.json (draft-07)."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft7Validator

C = Path(__file__).resolve().parents[2] / "contracts"


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


SCHEMA = load(C / "event.schema.json")
VALID = load(C / "examples" / "event_valid.json")
INVALID = load(C / "examples" / "event_invalid.json")
VALIDATOR = Draft7Validator(SCHEMA)


def errors(event: Any) -> list[Any]:
    return list(VALIDATOR.iter_errors(event))


def mutated(**changes: Any) -> dict[str, Any]:
    event = copy.deepcopy(VALID)
    for key, value in changes.items():
        if value is _DROP:
            del event[key]
        else:
            event[key] = value
    return event


_DROP = object()


def test_schema_is_draft07() -> None:
    assert SCHEMA["$schema"] == "http://json-schema.org/draft-07/schema#"
    Draft7Validator.check_schema(SCHEMA)


def test_valid_example_passes() -> None:
    assert errors(VALID) == []


def test_invalid_example_fails() -> None:
    assert errors(INVALID)


@pytest.mark.parametrize("field", ["ts_received", "ts_completed"])
def test_missing_timestamp_fails(field: str) -> None:
    assert errors(mutated(**{field: _DROP}))


@pytest.mark.parametrize("field", SCHEMA["required"])
def test_every_required_field_enforced(field: str) -> None:
    assert errors(mutated(**{field: _DROP}))


def test_post_create_accepts_null_resource_id() -> None:
    event = mutated(
        operation={"method": "POST", "path_template": "/orders"},
        resource_id=None,
        status_code=201,
    )
    assert errors(event) == []


def test_idempotency_key_optional_and_nullable() -> None:
    assert errors(mutated(idempotency_key=_DROP)) == []
    assert errors(mutated(idempotency_key=None)) == []
    assert errors(mutated(idempotency_key="k-1")) == []
    assert errors(mutated(idempotency_key=5))


@pytest.mark.parametrize(
    "event_id",
    [
        "e-000123",
        "3f2b8c1e-5d4a-1b7e-9a1c-0e6f2d8b7a41",  # version 1
        "3f2b8c1e-5d4a-4b7e-1a1c-0e6f2d8b7a41",  # bad variant
        "",
        7,
    ],
)
def test_event_id_must_be_uuid4(event_id: Any) -> None:
    assert errors(mutated(event_id=event_id))


@pytest.mark.parametrize(
    "ts",
    [
        "yesterday",
        "2026-10-13",
        "2026-10-13 09:00:00Z",
        "2026-13-13T09:00:00Z",
        "2026-10-13T09:00:00",
        5,
    ],
)
def test_bad_timestamps_fail(ts: Any) -> None:
    assert errors(mutated(ts_completed=ts))


@pytest.mark.parametrize("ts", ["2026-10-13T09:00:00Z", "2026-10-13T09:00:00.5+05:30"])
def test_good_timestamps_pass(ts: str) -> None:
    assert errors(mutated(ts_received=ts)) == []


@pytest.mark.parametrize("key", ["issuer", "sub", "role"])
def test_subject_requires_key(key: str) -> None:
    subject = {k: v for k, v in VALID["subject"].items() if k != key}
    assert errors(mutated(subject=subject))


def test_subject_must_be_object() -> None:
    assert errors(mutated(subject="user-A"))


@pytest.mark.parametrize("method", ["FETCH", "get", "", None])
def test_method_enum(method: Any) -> None:
    assert errors(mutated(operation={"method": method, "path_template": "/a"}))


@pytest.mark.parametrize("method", ["GET", "POST", "PUT", "PATCH", "DELETE"])
def test_methods_accepted(method: str) -> None:
    assert errors(mutated(operation={"method": method, "path_template": "/a"})) == []


@pytest.mark.parametrize("op", [{"method": "GET"}, {"path_template": "/a"}, "GET /a"])
def test_operation_shape(op: Any) -> None:
    assert errors(mutated(operation=op))


def test_path_template_must_start_with_slash() -> None:
    assert errors(mutated(operation={"method": "GET", "path_template": "orders"}))


@pytest.mark.parametrize("code", [99, 600, "200", 200.5, None])
def test_status_code_rejected(code: Any) -> None:
    assert errors(mutated(status_code=code))


@pytest.mark.parametrize("code", [100, 599])
def test_status_code_bounds_accepted(code: int) -> None:
    assert errors(mutated(status_code=code)) == []


def test_resource_type_and_id_types() -> None:
    assert errors(mutated(resource_type=None))
    assert errors(mutated(resource_id=1042))


def test_additional_properties_rejected() -> None:
    assert errors(mutated(extra="x"))
