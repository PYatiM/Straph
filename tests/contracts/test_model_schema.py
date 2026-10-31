"""Contract tests for contracts/model.schema.json (draft-07)."""

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


SCHEMA = load(C / "model.schema.json")
MODEL = load(C / "examples" / "model_checkout.json")
VALIDATOR = Draft7Validator(SCHEMA)


def errors(model: Any) -> list[Any]:
    return list(VALIDATOR.iter_errors(model))


def with_top(**changes: Any) -> dict[str, Any]:
    model = copy.deepcopy(MODEL)
    model.update(changes)
    return model


def with_transition(**changes: Any) -> dict[str, Any]:
    model = copy.deepcopy(MODEL)
    model["transitions"][0].update(changes)
    return model


def without_transition_key(key: str) -> dict[str, Any]:
    model = copy.deepcopy(MODEL)
    del model["transitions"][0][key]
    return model


def test_schema_is_valid_draft07() -> None:
    assert SCHEMA["$schema"] == "http://json-schema.org/draft-07/schema#"
    Draft7Validator.check_schema(SCHEMA)


def test_example_strictly_satisfies_schema() -> None:
    assert errors(MODEL) == []
    assert SCHEMA["additionalProperties"] is False


def test_example_exercises_every_enum_value() -> None:
    transitions = MODEL["transitions"]
    assert {t["provenance"] for t in transitions} >= {"spec", "trace", "both"}
    assert {t["review_status"] for t in transitions} >= {"confirmed", "candidate"}


@pytest.mark.parametrize("provenance", ["manual", "SPEC", "", None, 1])
def test_invalid_provenance_rejected(provenance: Any) -> None:
    assert errors(with_transition(provenance=provenance))


@pytest.mark.parametrize("provenance", ["spec", "trace", "both"])
def test_valid_provenance_accepted(provenance: str) -> None:
    assert errors(with_transition(provenance=provenance)) == []


@pytest.mark.parametrize("status", ["approved", "", None])
def test_invalid_review_status_rejected(status: Any) -> None:
    assert errors(with_transition(review_status=status))


@pytest.mark.parametrize("status", ["confirmed", "candidate", "rejected"])
def test_valid_review_status_accepted(status: str) -> None:
    assert errors(with_transition(review_status=status)) == []


@pytest.mark.parametrize("field", SCHEMA["required"])
def test_top_level_required(field: str) -> None:
    model = copy.deepcopy(MODEL)
    del model[field]
    assert errors(model)


@pytest.mark.parametrize("field", SCHEMA["properties"]["transitions"]["items"]["required"])
def test_transition_required(field: str) -> None:
    assert errors(without_transition_key(field))


@pytest.mark.parametrize("field", ["state_id", "terminal", "description"])
def test_state_required(field: str) -> None:
    model = copy.deepcopy(MODEL)
    del model["states"][0][field]
    assert errors(model)


@pytest.mark.parametrize(
    "version", ["checkout-v0", "", "zzzz1234@2026-10-13T09:00:00Z", "9fceb02d41", "9fceb02d41@now"]
)
def test_model_version_format(version: str) -> None:
    assert errors(with_top(model_version=version))


@pytest.mark.parametrize("empty", ["states", "transitions"])
def test_arrays_must_be_non_empty(empty: str) -> None:
    assert errors(with_top(**{empty: []}))


@pytest.mark.parametrize("field", ["from_states", "allowed_roles"])
def test_transition_lists_non_empty_and_unique(field: str) -> None:
    assert errors(with_transition(**{field: []}))
    assert errors(with_transition(**{field: ["a", "a"]}))


def test_operation_validation() -> None:
    assert errors(with_transition(operation={"method": "FETCH", "path_template": "/a"}))
    assert errors(with_transition(operation={"method": "GET", "path_template": "a"}))
    assert errors(with_transition(operation={"method": "GET"}))


def test_support_count_integer_non_negative() -> None:
    assert errors(with_transition(support_count=-1))
    assert errors(with_transition(support_count=1.5))
    assert errors(with_transition(support_count="3"))


def test_single_use_and_terminal_must_be_bool() -> None:
    assert errors(with_transition(single_use="yes"))
    model = copy.deepcopy(MODEL)
    model["states"][0]["terminal"] = "no"
    assert errors(model)


def test_additional_properties_rejected() -> None:
    assert errors(with_top(extra=1))
    assert errors(with_transition(extra=1))


def test_example_model_invariants() -> None:
    state_ids = [s["state_id"] for s in MODEL["states"]]
    assert len(state_ids) == len(set(state_ids))
    assert MODEL["initial_state"] in state_ids
    ids = [t["transition_id"] for t in MODEL["transitions"]]
    assert len(ids) == len(set(ids))
    targets: dict[tuple[str, str], set[str]] = {}
    for t in MODEL["transitions"]:
        assert set(t["from_states"]) <= set(state_ids)
        assert t["to_state"] in state_ids
        key = (t["operation"]["method"], t["operation"]["path_template"])
        targets.setdefault(key, set()).add(t["to_state"])
    assert all(len(v) == 1 for v in targets.values())
