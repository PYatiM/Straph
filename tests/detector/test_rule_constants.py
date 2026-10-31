"""Unit tests for detector rule constants and enums."""

import json
from pathlib import Path
from typing import Any

from services.detector.constants import (
    LEGAL_ADVANCE,
    RULE_ORDER,
    RuleId,
    SeverityLevel,
    ViolationLabel,
)

ROOT: Path = Path(__file__).resolve().parents[2]
ALERT_SCHEMA_PATH: Path = ROOT / "contracts" / "alert.schema.json"


def test_rule_order_contains_exactly_six_elements_in_invariant_order() -> None:
    """Assert RULE_ORDER contains exactly 6 elements in the specified invariant order."""
    expected_order: list[str] = ["R4", "R0", "LEGAL_ADVANCE", "R1", "R2", "R3"]
    assert len(RULE_ORDER) == 6
    assert RULE_ORDER == expected_order


def test_r1_precedes_r2_and_r3() -> None:
    """Assert R1 precedes R2 and R3 in the evaluation hierarchy."""
    idx_r1: int = RULE_ORDER.index(RuleId.R1)
    idx_r2: int = RULE_ORDER.index(RuleId.R2)
    idx_r3: int = RULE_ORDER.index(RuleId.R3)

    assert idx_r1 < idx_r2, f"Expected R1 (index {idx_r1}) to precede R2 (index {idx_r2})"
    assert idx_r1 < idx_r3, f"Expected R1 (index {idx_r1}) to precede R3 (index {idx_r3})"


def test_r4_unmodeled_is_initial_evaluation_step() -> None:
    """Assert R4 (unmodeled) is the initial evaluation step at index 0."""
    assert RULE_ORDER[0] == RuleId.R4
    assert RULE_ORDER.index(RuleId.R4) == 0


def test_legal_advance_position() -> None:
    """Assert LEGAL_ADVANCE is evaluated after R0 and before R1."""
    idx_r0: int = RULE_ORDER.index(RuleId.R0)
    idx_legal: int = RULE_ORDER.index(LEGAL_ADVANCE)
    idx_r1: int = RULE_ORDER.index(RuleId.R1)

    assert idx_r0 < idx_legal < idx_r1


def test_violation_label_enum_values() -> None:
    """Assert ViolationLabel enum defines all expected labels."""
    expected_labels: set[str] = {
        "SKIPPED",
        "OUT_OF_ORDER",
        "REPLAYED",
        "IDENTITY_MISMATCH",
        "UNMODELED",
    }
    actual_labels: set[str] = {label.value for label in ViolationLabel}
    assert actual_labels == expected_labels

    for member in ViolationLabel:
        assert isinstance(member, str)
        assert member == member.value


def test_rule_id_enum_values() -> None:
    """Assert RuleId enum defines all expected detection rule identifiers."""
    expected_rules: set[str] = {"R0", "R1", "R2", "R3", "R4"}
    actual_rules: set[str] = {rule.value for rule in RuleId}
    assert actual_rules == expected_rules

    for member in RuleId:
        assert isinstance(member, str)
        assert member == member.value


def test_severity_level_enum_values() -> None:
    """Assert SeverityLevel enum defines attempted and accepted."""
    expected_severities: set[str] = {"attempted", "accepted"}
    actual_severities: set[str] = {sev.value for sev in SeverityLevel}
    assert actual_severities == expected_severities

    for member in SeverityLevel:
        assert isinstance(member, str)
        assert member == member.value


def test_enums_consistent_with_alert_schema() -> None:
    """Assert enums strictly match the JSON schema definition in alert.schema.json."""
    schema: dict[str, Any] = json.loads(ALERT_SCHEMA_PATH.read_text(encoding="utf-8"))
    properties: dict[str, Any] = schema["properties"]

    schema_labels: set[str] = set(properties["label"]["enum"])
    schema_rules: set[str] = set(properties["rule"]["enum"])
    schema_severities: set[str] = set(properties["severity"]["enum"])

    assert {label.value for label in ViolationLabel} == schema_labels
    assert {rule.value for rule in RuleId} == schema_rules
    assert {sev.value for sev in SeverityLevel} == schema_severities
