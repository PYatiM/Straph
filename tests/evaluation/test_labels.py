"""Unit tests for GroundTruthLabel, JSONLLabelWriter, and JSONLLabelReader."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from pydantic import ValidationError

from services.evaluation.labels import (
    ExpectedLabel,
    ExpectedRule,
    GroundTruthLabel,
    GroundTruthLabelDataclass,
    JSONLLabelReader,
    JSONLLabelWriter,
)

SAMPLE_UUID1 = "00000002-0000-4000-8000-000000000003"
SAMPLE_UUID2 = "4b6e50e8-0797-4d7a-b9c7-5f72e61cbb11"
SAMPLE_UUID3 = "9d36a962-d9fa-48ef-bf46-f949c20fbf28"


@pytest.fixture
def sample_violation_label() -> GroundTruthLabel:
    return GroundTruthLabel(
        event_id=SAMPLE_UUID1,
        is_violation=True,
        expected_label=ExpectedLabel.SKIPPED,
        expected_rule=ExpectedRule.R2,
        missing_ops=["POST /orders/{id}/payment", "POST /payments/webhook"],
        scenario_id="TC2_SKIPPED_PAYMENT",
        seed=42,
    )


@pytest.fixture
def sample_benign_label() -> GroundTruthLabel:
    return GroundTruthLabel(
        event_id=SAMPLE_UUID2,
        is_violation=False,
        expected_label=ExpectedLabel.BENIGN,
        expected_rule=None,
        missing_ops=[],
        scenario_id="TC1_LEGAL_PATH",
        seed=101,
    )


# ==============================================================================
# Model Validations
# ==============================================================================


def test_valid_label_creation(sample_violation_label: GroundTruthLabel) -> None:
    assert sample_violation_label.event_id == SAMPLE_UUID1
    assert sample_violation_label.is_violation is True
    assert sample_violation_label.expected_label == "SKIPPED"
    assert sample_violation_label.expected_rule == "R2"
    assert sample_violation_label.missing_ops == [
        "POST /orders/{id}/payment",
        "POST /payments/webhook",
    ]
    assert sample_violation_label.scenario_id == "TC2_SKIPPED_PAYMENT"
    assert sample_violation_label.seed == 42


def test_benign_label_nullable_rule(sample_benign_label: GroundTruthLabel) -> None:
    assert sample_benign_label.expected_rule is None
    assert sample_benign_label.is_violation is False
    assert sample_benign_label.expected_label == "BENIGN"
    assert sample_benign_label.missing_ops == []


@pytest.mark.parametrize(
    "label_val",
    ["BENIGN", "SKIPPED", "OUT_OF_ORDER", "REPLAYED", "IDENTITY_MISMATCH", "UNMODELED"],
)
def test_all_expected_labels_accepted(label_val: str) -> None:
    label = GroundTruthLabel(
        event_id=SAMPLE_UUID1,
        is_violation=label_val != "BENIGN",
        expected_label=label_val,  # type: ignore[arg-type]
        scenario_id="TEST_SCENARIO",
        seed=1,
    )
    assert label.expected_label == label_val


def test_invalid_expected_label_rejected() -> None:
    with pytest.raises(ValidationError):
        GroundTruthLabel(
            event_id=SAMPLE_UUID1,
            is_violation=True,
            expected_label="UNKNOWN_VIOLATION",  # type: ignore[arg-type]
            scenario_id="TEST",
            seed=1,
        )


@pytest.mark.parametrize("rule_val", ["R0", "R1", "R2", "R3", "R4", None])
def test_all_expected_rules_accepted(rule_val: str | None) -> None:
    label = GroundTruthLabel(
        event_id=SAMPLE_UUID1,
        is_violation=True,
        expected_label=ExpectedLabel.SKIPPED,
        expected_rule=rule_val,  # type: ignore[arg-type]
        scenario_id="TEST",
        seed=1,
    )
    assert label.expected_rule == rule_val


def test_invalid_expected_rule_rejected() -> None:
    with pytest.raises(ValidationError):
        GroundTruthLabel(
            event_id=SAMPLE_UUID1,
            is_violation=True,
            expected_label=ExpectedLabel.SKIPPED,
            expected_rule="R99",  # type: ignore[arg-type]
            scenario_id="TEST",
            seed=1,
        )


@pytest.mark.parametrize(
    "bad_uuid", ["not-a-uuid", "1234", "", "00000000-0000-1000-0000-000000000000"]
)
def test_invalid_uuid4_rejected(bad_uuid: str) -> None:
    with pytest.raises(ValidationError):
        GroundTruthLabel(
            event_id=bad_uuid,
            is_violation=False,
            expected_label=ExpectedLabel.BENIGN,
            scenario_id="TEST",
            seed=1,
        )


def test_empty_scenario_id_rejected() -> None:
    with pytest.raises(ValidationError):
        GroundTruthLabel(
            event_id=SAMPLE_UUID1,
            is_violation=False,
            expected_label=ExpectedLabel.BENIGN,
            scenario_id="",
            seed=1,
        )


# ==============================================================================
# Dataclass Conversion
# ==============================================================================


def test_dataclass_roundtrip(sample_violation_label: GroundTruthLabel) -> None:
    dc = sample_violation_label.to_dataclass()
    assert isinstance(dc, GroundTruthLabelDataclass)
    assert dc.event_id == sample_violation_label.event_id
    assert dc.expected_label == ExpectedLabel.SKIPPED
    assert dc.expected_rule == ExpectedRule.R2
    assert dc.missing_ops == sample_violation_label.missing_ops

    # Convert back from dataclass
    restored = GroundTruthLabel.from_dataclass(dc)
    assert restored == sample_violation_label

    # Via dataclass method
    assert dc.to_pydantic() == sample_violation_label


# ==============================================================================
# Serialization Roundtrips with JSONLLabelWriter and JSONLLabelReader
# ==============================================================================


def test_file_serialization_roundtrip(
    tmp_path: Path,
    sample_violation_label: GroundTruthLabel,
    sample_benign_label: GroundTruthLabel,
) -> None:
    file_path = tmp_path / "dataset.labels.jsonl"
    labels = [sample_violation_label, sample_benign_label]

    with JSONLLabelWriter(file_path) as writer:
        count = writer.write_all(labels)
        assert count == 2

    assert file_path.is_file()

    with JSONLLabelReader(file_path) as reader:
        read_labels = reader.read_all()

    assert len(read_labels) == 2
    assert read_labels[0] == sample_violation_label
    assert read_labels[1] == sample_benign_label


def test_in_memory_streaming_roundtrip(
    sample_violation_label: GroundTruthLabel,
    sample_benign_label: GroundTruthLabel,
) -> None:
    stream = io.StringIO()
    writer = JSONLLabelWriter(stream)
    writer.write(sample_violation_label)
    writer.write(sample_benign_label)

    # Rewind and read
    stream.seek(0)
    reader = JSONLLabelReader(stream)
    items = list(reader)

    assert len(items) == 2
    assert items[0] == sample_violation_label
    assert items[1] == sample_benign_label


def test_writer_accepts_dataclass_and_dict(
    sample_violation_label: GroundTruthLabel,
) -> None:
    stream = io.StringIO()
    with JSONLLabelWriter(stream) as writer:
        writer.write(sample_violation_label.to_dataclass())
        writer.write(sample_violation_label.model_dump())

    stream.seek(0)
    with JSONLLabelReader(stream) as reader:
        items = reader.read_all()

    assert len(items) == 2
    assert items[0] == sample_violation_label
    assert items[1] == sample_violation_label


def test_reader_ignores_comments_and_empty_lines() -> None:
    raw_content = (
        f"# Header comment\n\n"
        f'{{"event_id": "{SAMPLE_UUID3}", "is_violation": false, "expected_label": "BENIGN", "scenario_id": "TEST", "seed": 0}}\n'
        f"   \n"
        f"# Another comment\n"
    )
    stream = io.StringIO(raw_content)
    with JSONLLabelReader(stream) as reader:
        items = reader.read_all()

    assert len(items) == 1
    assert items[0].event_id == SAMPLE_UUID3
    assert items[0].expected_label == "BENIGN"


def test_reader_raises_on_invalid_json() -> None:
    stream = io.StringIO('{"event_id": "invalid json line\n')
    with pytest.raises(ValueError, match="Error parsing label record on line 1"):
        list(JSONLLabelReader(stream))


def test_reader_missing_file_raises(tmp_path: Path) -> None:
    missing_file = tmp_path / "does_not_exist.labels.jsonl"
    with pytest.raises(FileNotFoundError):
        JSONLLabelReader(missing_file).open()
