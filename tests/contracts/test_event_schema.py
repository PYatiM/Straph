"""Contract tests for the event schema."""

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft7Validator

# ---------------------------------------------------------------------------
# Paths and fixtures
# ---------------------------------------------------------------------------

C = Path(__file__).resolve().parents[2] / "contracts"


def load(filename: str) -> Any:
    """Load a JSON file from the contracts directory."""
    with (C / filename).open(encoding="utf-8") as file:
        return json.load(file)


SCHEMA = load("event.schema.json")
VALID = load("event.valid.json")
INVALID = load("event.invalid.json")

VALIDATOR = Draft7Validator(SCHEMA)


def errors(event: dict[str, Any]) -> list[Any]:
    """Return schema validation errors for an event."""
    return list(VALIDATOR.iter_errors(event))


def mutated(**changes: Any) -> dict[str, Any]:
    """Return a copy of the valid event with selected fields changed."""
    event = copy.deepcopy(VALID)

    for key, value in changes.items():
        if value is _DROP:
            event.pop(key, None)
        else:
            event[key] = value

    return event


_DROP = object()


# ---------------------------------------------------------------------------
# Schema and example tests
# ---------------------------------------------------------------------------


def test_schema_uses_draft_7() -> None:
    """Ensure the schema declares JSON Schema Draft 7."""
    assert SCHEMA.get("$schema") == ("http://json-schema.org/draft-07/schema#")


def test_valid_example_is_accepted() -> None:
    """Ensure the valid example passes schema validation."""
    assert errors(VALID) == []


def test_invalid_example_is_rejected() -> None:
    """Ensure the invalid example fails schema validation."""
    assert errors(INVALID)


def test_required_fields_are_enforced() -> None:
    """Ensure every required field is necessary."""
    required = SCHEMA.get("required", [])

    assert required

    for field in required:
        event = copy.deepcopy(VALID)
        event.pop(field, None)

        assert errors(event), f"Missing required field was accepted: {field}"


# ---------------------------------------------------------------------------
# Idempotency key tests
# ---------------------------------------------------------------------------


def test_idempotency_key_optional_and_nullable() -> None:
    """Ensure idempotency_key may be omitted, null, or a string."""
    assert errors(mutated(idempotency_key=_DROP)) == []
    assert errors(mutated(idempotency_key=None)) == []
    assert errors(mutated(idempotency_key="k-1")) == []


@pytest.mark.parametrize(
    "bad_key",
    [
        5,
        [],
        {},
        True,
    ],
)
def test_invalid_idempotency_key_rejected(bad_key: Any) -> None:
    """Ensure invalid idempotency_key types are rejected."""
    assert errors(mutated(idempotency_key=bad_key))


# ---------------------------------------------------------------------------
# Event ID tests
# ---------------------------------------------------------------------------


def test_event_id_must_be_uuid4() -> None:
    """Ensure event_id follows the UUID4 format."""
    assert errors(mutated(event_id="not-a-uuid"))

    assert errors(mutated(event_id="550e8400-e29b-41d4-a716-446655440000")) == []


# ---------------------------------------------------------------------------
# Timestamp tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "timestamp",
    [
        "not-a-timestamp",
        "",
        123,
    ],
)
def test_invalid_timestamps_rejected(timestamp: Any) -> None:
    """Ensure invalid timestamps are rejected."""
    assert errors(mutated(timestamp=timestamp))


# ---------------------------------------------------------------------------
# Subject tests
# ---------------------------------------------------------------------------


def test_subject_must_be_an_object() -> None:
    """Ensure subject has the expected object structure."""
    assert errors(mutated(subject="user-123"))


# ---------------------------------------------------------------------------
# HTTP method tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "method",
    [
        "INVALID",
        "",
        123,
    ],
)
def test_invalid_http_methods_rejected(method: Any) -> None:
    """Ensure unsupported HTTP methods are rejected."""
    assert errors(mutated(http_method=method))


# ---------------------------------------------------------------------------
# Operation tests
# ---------------------------------------------------------------------------


def test_operation_must_be_an_object() -> None:
    """Ensure operation has the expected object structure."""
    assert errors(mutated(operation="create"))


# ---------------------------------------------------------------------------
# Status code tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "status_code",
    [
        "200",
        99,
        600,
    ],
)
def test_invalid_status_codes_rejected(status_code: Any) -> None:
    """Ensure invalid status codes are rejected."""
    assert errors(mutated(status_code=status_code))


# ---------------------------------------------------------------------------
# Resource type tests
# ---------------------------------------------------------------------------


def test_resource_type_must_be_a_string() -> None:
    """Ensure resource_type must be a string."""
    assert errors(mutated(resource_type=123))
    assert errors(mutated(resource_type=None))
    assert errors(mutated(resource_type="order")) == []


# ---------------------------------------------------------------------------
# Additional properties tests
# ---------------------------------------------------------------------------


def test_additional_properties_rejected() -> None:
    """Ensure undeclared event properties are rejected."""
    assert errors(mutated(unexpected_field="unexpected"))
