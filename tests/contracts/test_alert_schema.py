"""Contract tests for alert schema validation."""

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

ROOT: Path = Path(__file__).resolve().parents[2]
CONTRACTS_DIR: Path = ROOT / "contracts"


def load_json(path: Path) -> Any:
    """Load and parse a JSON file."""
    return json.loads(path.read_text(encoding="utf-8"))


def get_alert_validator() -> Draft202012Validator:
    """Load the alert schema and return a validated Draft202012Validator."""
    schema: dict[str, Any] = load_json(CONTRACTS_DIR / "alert.schema.json")
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


@pytest.fixture
def alert_validator() -> Draft202012Validator:
    """Pytest fixture providing the alert schema validator."""
    return get_alert_validator()


@pytest.fixture
def valid_alert_payload() -> dict[str, Any]:
    """Provide a minimal valid alert payload conforming to alert.schema.json."""
    return {
        "alert_id": "4b6e50e8-0797-4d7a-b9c7-5f72e61cbb11",
        "event_id": "9d36a962-d9fa-48ef-bf46-f949c20fbf28",
        "timestamp": "2026-10-29T12:00:00Z",
        "label": "SKIPPED",
        "rule": "R2",
        "severity": "accepted",
        "subject": {
            "identity": "user-1842",
            "role": "customer",
        },
        "resource": {
            "type": "Order",
            "id": "5521",
        },
        "expected_path": ["create", "pay", "confirm"],
        "observed": {
            "operation": "confirm",
            "state": "S1",
            "status_code": 200,
        },
        "missing": ["pay"],
        "owasp_class": "BLA6",
        "flags": {
            "tainted": True,
            "late": False,
            "retry": False,
        },
        "model_version": "checkout-v0",
        "rank_score": None,
    }


def test_schema_itself_is_valid() -> None:
    """Ensure alert.schema.json conforms to draft 2020-12 meta-schema."""
    schema: dict[str, Any] = load_json(CONTRACTS_DIR / "alert.schema.json")
    Draft202012Validator.check_schema(schema)


def test_valid_example_file(alert_validator: Draft202012Validator) -> None:
    """Validate contracts/examples/alert_valid.json against the schema."""
    example: dict[str, Any] = load_json(CONTRACTS_DIR / "examples" / "alert_valid.json")
    errors: list[Any] = list(alert_validator.iter_errors(example))
    assert errors == [], f"Expected no validation errors, got: {errors}"


def test_valid_payload_passes(
    alert_validator: Draft202012Validator,
    valid_alert_payload: dict[str, Any],
) -> None:
    """Ensure the base fixture payload passes validation."""
    assert alert_validator.is_valid(valid_alert_payload)


@pytest.mark.parametrize(
    ("rule", "label"),
    [
        ("R0", "IDENTITY_MISMATCH"),
        ("R1", "REPLAYED"),
        ("R2", "SKIPPED"),
        ("R3", "OUT_OF_ORDER"),
        ("R4", "UNMODELED"),
    ],
)
def test_all_valid_rules_and_labels_pass(
    alert_validator: Draft202012Validator,
    valid_alert_payload: dict[str, Any],
    rule: str,
    label: str,
) -> None:
    """Verify each allowed rule and label combination is valid."""
    payload: dict[str, Any] = copy.deepcopy(valid_alert_payload)
    payload["rule"] = rule
    payload["label"] = label
    assert alert_validator.is_valid(payload)


@pytest.mark.parametrize("severity", ["attempted", "accepted"])
def test_valid_severities_pass(
    alert_validator: Draft202012Validator,
    valid_alert_payload: dict[str, Any],
    severity: str,
) -> None:
    """Verify attempted and accepted severities pass."""
    payload: dict[str, Any] = copy.deepcopy(valid_alert_payload)
    payload["severity"] = severity
    assert alert_validator.is_valid(payload)


@pytest.mark.parametrize(
    "rank_score",
    [None, 0.0, 0.42, 1.0],
)
def test_valid_rank_score_values_pass(
    alert_validator: Draft202012Validator,
    valid_alert_payload: dict[str, Any],
    rank_score: float | None,
) -> None:
    """Verify rank_score accepts null and floats in [0.0, 1.0]."""
    payload: dict[str, Any] = copy.deepcopy(valid_alert_payload)
    payload["rank_score"] = rank_score
    assert alert_validator.is_valid(payload)


def test_optional_rank_score_omitted_passes(
    alert_validator: Draft202012Validator,
    valid_alert_payload: dict[str, Any],
) -> None:
    """Verify rank_score can be omitted since it is optional."""
    payload: dict[str, Any] = copy.deepcopy(valid_alert_payload)
    del payload["rank_score"]
    assert alert_validator.is_valid(payload)


def test_nullable_observed_state_passes(
    alert_validator: Draft202012Validator,
    valid_alert_payload: dict[str, Any],
) -> None:
    """Verify observed.state can be null."""
    payload: dict[str, Any] = copy.deepcopy(valid_alert_payload)
    payload["observed"]["state"] = None
    assert alert_validator.is_valid(payload)


@pytest.mark.parametrize(
    "bad_rule",
    ["R5", "R-1", "R", "r0", "r2", "UNKNOWN", "RULE_0", "", 0],
)
def test_reject_unknown_rule_strings(
    alert_validator: Draft202012Validator,
    valid_alert_payload: dict[str, Any],
    bad_rule: Any,
) -> None:
    """Ensure the schema rejects unknown rule values."""
    payload: dict[str, Any] = copy.deepcopy(valid_alert_payload)
    payload["rule"] = bad_rule
    errors: list[Any] = list(alert_validator.iter_errors(payload))
    assert len(errors) > 0, f"Expected validation failure for rule={bad_rule!r}"


@pytest.mark.parametrize(
    "bad_label",
    ["LEGAL", "UNKNOWN", "skipped", "INVALID", ""],
)
def test_reject_unknown_labels(
    alert_validator: Draft202012Validator,
    valid_alert_payload: dict[str, Any],
    bad_label: str,
) -> None:
    """Ensure the schema rejects unlisted label values."""
    payload: dict[str, Any] = copy.deepcopy(valid_alert_payload)
    payload["label"] = bad_label
    assert not alert_validator.is_valid(payload)


@pytest.mark.parametrize(
    "bad_severity",
    ["denied", "critical", "high", "low", "ACCEPTED", ""],
)
def test_reject_unknown_severity(
    alert_validator: Draft202012Validator,
    valid_alert_payload: dict[str, Any],
    bad_severity: str,
) -> None:
    """Ensure the schema rejects unlisted severity values."""
    payload: dict[str, Any] = copy.deepcopy(valid_alert_payload)
    payload["severity"] = bad_severity
    assert not alert_validator.is_valid(payload)


@pytest.mark.parametrize(
    "required_field",
    [
        "alert_id",
        "event_id",
        "timestamp",
        "label",
        "rule",
        "severity",
        "subject",
        "resource",
        "expected_path",
        "observed",
        "missing",
        "owasp_class",
        "flags",
        "model_version",
    ],
)
def test_reject_missing_required_fields(
    alert_validator: Draft202012Validator,
    valid_alert_payload: dict[str, Any],
    required_field: str,
) -> None:
    """Ensure dropping any required field causes a validation error."""
    payload: dict[str, Any] = copy.deepcopy(valid_alert_payload)
    del payload[required_field]
    errors: list[Any] = list(alert_validator.iter_errors(payload))
    assert len(errors) > 0
    assert any(required_field in err.message for err in errors)


@pytest.mark.parametrize(
    "field",
    ["alert_id", "event_id"],
)
@pytest.mark.parametrize(
    "invalid_uuid",
    [
        "not-a-uuid",
        "12345",
        "6ba7b810-9dad-11d1-80b4-00c04fd430c8",  # UUIDv1
        "00000000-0000-0000-0000-000000000000",  # nil UUID
        "",
    ],
)
def test_reject_invalid_uuid4_fields(
    alert_validator: Draft202012Validator,
    valid_alert_payload: dict[str, Any],
    field: str,
    invalid_uuid: str,
) -> None:
    """Ensure non-UUID4 strings are rejected for alert_id and event_id."""
    payload: dict[str, Any] = copy.deepcopy(valid_alert_payload)
    payload[field] = invalid_uuid
    assert not alert_validator.is_valid(payload)


@pytest.mark.parametrize(
    "bad_rank_score",
    [-0.01, -1.0, 1.01, 2.5, "0.5"],
)
def test_reject_out_of_range_rank_score(
    alert_validator: Draft202012Validator,
    valid_alert_payload: dict[str, Any],
    bad_rank_score: Any,
) -> None:
    """Ensure rank_score outside [0.0, 1.0] or non-numeric is rejected."""
    payload: dict[str, Any] = copy.deepcopy(valid_alert_payload)
    payload["rank_score"] = bad_rank_score
    assert not alert_validator.is_valid(payload)


def test_reject_additional_top_level_properties(
    alert_validator: Draft202012Validator,
    valid_alert_payload: dict[str, Any],
) -> None:
    """Ensure unknown top-level properties are rejected."""
    payload: dict[str, Any] = copy.deepcopy(valid_alert_payload)
    payload["extra_property"] = "disallowed"
    assert not alert_validator.is_valid(payload)


@pytest.mark.parametrize("missing_resource_field", ["type", "id"])
def test_reject_incomplete_resource(
    alert_validator: Draft202012Validator,
    valid_alert_payload: dict[str, Any],
    missing_resource_field: str,
) -> None:
    """Ensure resource requires both type and id."""
    payload: dict[str, Any] = copy.deepcopy(valid_alert_payload)
    del payload["resource"][missing_resource_field]
    assert not alert_validator.is_valid(payload)


@pytest.mark.parametrize("missing_observed_field", ["operation", "state", "status_code"])
def test_reject_incomplete_observed(
    alert_validator: Draft202012Validator,
    valid_alert_payload: dict[str, Any],
    missing_observed_field: str,
) -> None:
    """Ensure observed requires operation, state, and status_code."""
    payload: dict[str, Any] = copy.deepcopy(valid_alert_payload)
    del payload["observed"][missing_observed_field]
    assert not alert_validator.is_valid(payload)


@pytest.mark.parametrize("missing_flag", ["tainted", "late", "retry"])
def test_reject_incomplete_flags(
    alert_validator: Draft202012Validator,
    valid_alert_payload: dict[str, Any],
    missing_flag: str,
) -> None:
    """Ensure flags requires tainted, late, and retry."""
    payload: dict[str, Any] = copy.deepcopy(valid_alert_payload)
    del payload["flags"][missing_flag]
    assert not alert_validator.is_valid(payload)


@pytest.mark.parametrize("secondary_label", ["CANCELLED", None])
def test_optional_secondary_label_passes(
    alert_validator: Draft202012Validator,
    valid_alert_payload: dict[str, Any],
    secondary_label: str | None,
) -> None:
    """Verify secondary_label accepts a non-empty string or null."""
    payload: dict[str, Any] = copy.deepcopy(valid_alert_payload)
    payload["secondary_label"] = secondary_label
    assert alert_validator.is_valid(payload)


@pytest.mark.parametrize("bad_secondary_label", ["", 5, ["CANCELLED"]])
def test_reject_invalid_secondary_label(
    alert_validator: Draft202012Validator,
    valid_alert_payload: dict[str, Any],
    bad_secondary_label: Any,
) -> None:
    """Ensure secondary_label rejects empty strings and non-string values."""
    payload: dict[str, Any] = copy.deepcopy(valid_alert_payload)
    payload["secondary_label"] = bad_secondary_label
    assert not alert_validator.is_valid(payload)


def test_secondary_label_not_required(
    alert_validator: Draft202012Validator,
    valid_alert_payload: dict[str, Any],
) -> None:
    """Ensure secondary_label is optional (absent from the base payload)."""
    assert "secondary_label" not in valid_alert_payload
    assert alert_validator.is_valid(valid_alert_payload)
