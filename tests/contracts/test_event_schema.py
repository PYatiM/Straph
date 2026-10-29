"""Contract tests for the optional idempotency_key in event.schema.json."""

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

ROOT: Path = Path(__file__).resolve().parents[2]
CONTRACTS_DIR: Path = ROOT / "contracts"


@pytest.fixture
def event_validator() -> Draft202012Validator:
    """Provide a validator for event.schema.json."""
    schema: dict[str, Any] = json.loads(
        (CONTRACTS_DIR / "event.schema.json").read_text(encoding="utf-8")
    )
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


@pytest.fixture
def valid_event() -> dict[str, Any]:
    """Provide the valid example event."""
    path: Path = CONTRACTS_DIR / "examples" / "event_valid.json"
    return json.loads(path.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def test_idempotency_key_is_optional(
    event_validator: Draft202012Validator, valid_event: dict[str, Any]
) -> None:
    """Ensure events without idempotency_key remain valid."""
    assert "idempotency_key" not in valid_event
    assert event_validator.is_valid(valid_event)


def test_idempotency_key_accepted(
    event_validator: Draft202012Validator, valid_event: dict[str, Any]
) -> None:
    """Ensure a non-empty idempotency_key string is accepted."""
    payload: dict[str, Any] = copy.deepcopy(valid_event)
    payload["idempotency_key"] = "idemp-999"
    assert event_validator.is_valid(payload)


@pytest.mark.parametrize("bad_key", ["", 5, None, ["k"]])
def test_invalid_idempotency_key_rejected(
    event_validator: Draft202012Validator, valid_event: dict[str, Any], bad_key: Any
) -> None:
    """Ensure empty or non-string idempotency_key values are rejected."""
    payload: dict[str, Any] = copy.deepcopy(valid_event)
    payload["idempotency_key"] = bad_key
    assert not event_validator.is_valid(payload)
