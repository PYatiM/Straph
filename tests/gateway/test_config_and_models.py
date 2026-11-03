"""Tests for services/gateway/config.py and models.py."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from jsonschema import Draft7Validator
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[2]
CONTRACTS = ROOT / "contracts"


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        f"gateway_{name}", ROOT / "services" / "gateway" / f"{name}.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


config = _load("config")
models = _load("models")

VALID_TEXT = (CONTRACTS / "examples" / "event_valid.json").read_text(encoding="utf-8")
VALID: dict[str, Any] = json.loads(VALID_TEXT)
VALIDATOR = Draft7Validator(json.loads((CONTRACTS / "event.schema.json").read_text("utf-8")))


# ---- config ----


def test_config_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (
        "KAFKA_BOOTSTRAP_SERVERS",
        "EVENTS_TOPIC",
        "GATEWAY_ENABLED",
        "IDEMPOTENCY_HEADER",
    ):
        monkeypatch.delenv(var, raising=False)
    s = config.GatewaySettings()
    assert s.kafka_bootstrap_servers == "localhost:9092"
    assert s.events_topic == "api.events"
    assert s.gateway_enabled is True
    assert s.idempotency_header == "X-Idempotency-Key"


def test_config_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KAFKA_BOOTSTRAP_SERVERS", "k:1")
    monkeypatch.setenv("EVENTS_TOPIC", "t")
    monkeypatch.setenv("GATEWAY_ENABLED", "false")
    monkeypatch.setenv("IDEMPOTENCY_HEADER", "X-Key")
    s = config.get_settings()
    assert (s.kafka_bootstrap_servers, s.events_topic) == ("k:1", "t")
    assert s.gateway_enabled is False
    assert s.idempotency_header == "X-Key"


def test_config_rejects_bad_bool(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GATEWAY_ENABLED", "maybe")
    with pytest.raises(ValidationError):
        config.GatewaySettings()


# ---- models ----


def test_serialization_matches_contract_character_for_character() -> None:
    payload = models.APIEventPayload.model_validate(VALID)
    assert payload.to_contract_json() == json.dumps(VALID)
    assert json.loads(payload.to_contract_json()) == VALID


def test_serialized_output_is_schema_valid() -> None:
    payload = models.APIEventPayload.model_validate(VALID)
    assert list(VALIDATOR.iter_errors(payload.to_contract_dict())) == []


def test_parse_from_json_text() -> None:
    payload = models.APIEventPayload.model_validate_json(VALID_TEXT)
    assert payload.status_code == 202
    assert payload.idempotency_key is None


def test_optional_idempotency_key_defaults_none_and_serializes_null() -> None:
    data = {k: v for k, v in VALID.items() if k != "idempotency_key"}
    payload = models.APIEventPayload.model_validate(data)
    assert payload.to_contract_dict()["idempotency_key"] is None


def test_null_resource_id_allowed() -> None:
    payload = models.APIEventPayload.model_validate({**VALID, "resource_id": None})
    assert list(VALIDATOR.iter_errors(payload.to_contract_dict())) == []


@pytest.mark.parametrize(
    "field",
    [
        "event_id",
        "ts_received",
        "ts_completed",
        "subject",
        "session_id",
        "token_id",
        "operation",
        "resource_type",
        "resource_id",
        "status_code",
    ],
)
def test_missing_mandatory_field_fails(field: str) -> None:
    data = {k: v for k, v in VALID.items() if k != field}
    with pytest.raises(ValidationError) as exc:
        models.APIEventPayload.model_validate(data)
    assert any(e["loc"] == (field,) and e["type"] == "missing" for e in exc.value.errors())


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("event_id", "not-a-uuid"),
        ("ts_received", "2026-10-13 09:00:00"),
        ("status_code", 99),
        ("status_code", 600),
        ("status_code", "202"),
        ("operation", {"method": "TRACE", "path_template": "/x"}),
        ("operation", {"method": "GET", "path_template": "x"}),
        ("subject", {"issuer": "", "sub": "u", "role": "r"}),
        ("subject", {"issuer": "i", "sub": "u"}),
        ("unexpected", 1),
    ],
)
def test_invalid_values_fail_and_schema_agrees(field: str, value: Any) -> None:
    data = {**VALID, field: value}
    assert list(VALIDATOR.iter_errors(data)) != []
    with pytest.raises(ValidationError):
        models.APIEventPayload.model_validate(data)
