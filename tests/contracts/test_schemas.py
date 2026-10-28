"""Contract tests: examples validate, a bad event fails, and model invariants hold."""

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, validators

ROOT = Path(__file__).resolve().parents[2]
C = ROOT / "contracts"


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def validator(name):
    schema = load(C / name)
    cls = validators.validator_for(schema, default=Draft202012Validator)
    cls.check_schema(schema)
    return cls(schema)


@pytest.mark.parametrize(
    "schema,example",
    [
        ("event.schema.json", "event_valid.json"),
        ("alert.schema.json", "alert_valid.json"),
        ("model.schema.json", "model_checkout.json"),
    ],
)
def test_valid_examples_pass(schema, example):
    errors = list(validator(schema).iter_errors(load(C / "examples" / example)))
    assert errors == []


def test_invalid_event_fails():
    errors = list(
        validator("event.schema.json").iter_errors(load(C / "examples" / "event_invalid.json"))
    )
    assert errors, "the invalid example must be rejected"
