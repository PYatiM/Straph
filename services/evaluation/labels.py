"""Ground-truth labels data structures and JSONL streaming I/O for evaluation."""

from __future__ import annotations

import io
import json
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from types import TracebackType
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator

_UUID4_PATTERN = (
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-4[0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$"
)
_UUID4_RE = re.compile(_UUID4_PATTERN)


class ExpectedLabel(StrEnum):
    """Evaluation ground truth classification label."""

    BENIGN = "BENIGN"
    SKIPPED = "SKIPPED"
    OUT_OF_ORDER = "OUT_OF_ORDER"
    REPLAYED = "REPLAYED"
    IDENTITY_MISMATCH = "IDENTITY_MISMATCH"
    UNMODELED = "UNMODELED"


class ExpectedRule(StrEnum):
    """Detection rule id associated with the violation."""

    R0 = "R0"
    R1 = "R1"
    R2 = "R2"
    R3 = "R3"
    R4 = "R4"


@dataclass(slots=True)
class GroundTruthLabelDataclass:
    """Standard dataclass representation of a ground-truth label."""

    event_id: str
    is_violation: bool
    expected_label: ExpectedLabel | str
    expected_rule: ExpectedRule | str | None = None
    missing_ops: list[str] = field(default_factory=list)
    scenario_id: str = ""
    seed: int = 0

    def to_pydantic(self) -> GroundTruthLabel:
        return GroundTruthLabel.model_validate(self)


class GroundTruthLabel(BaseModel):
    """Pydantic model representing a ground-truth evaluation label for an API event."""

    model_config = ConfigDict(
        extra="forbid",
        from_attributes=True,
        populate_by_name=True,
        use_enum_values=True,
    )

    event_id: str = Field(description="UUID4 string identifying the observed event.")
    is_violation: bool = Field(
        description="True if the event represents an authorization or workflow violation."
    )
    expected_label: ExpectedLabel = Field(description="Expected evaluation classification label.")
    expected_rule: ExpectedRule | None = Field(
        default=None,
        description="Expected detection rule triggered (e.g. R0..R4), or None if benign.",
    )
    missing_ops: list[str] = Field(
        default_factory=list,
        description="List of operation strings skipped or expected before this event.",
    )
    scenario_id: str = Field(description="Scenario identifier string, e.g. TC2_SKIPPED_PAYMENT.")
    seed: int = Field(default=0, description="Seed used for reproducible trace generation.")

    @field_validator("event_id")
    @classmethod
    def _validate_uuid4(cls, value: str) -> str:
        if not isinstance(value, str) or not _UUID4_RE.match(value.strip()):
            raise ValueError(f"event_id must be a valid UUID4 string, got: {value!r}")
        return value.strip().lower()

    @field_validator("scenario_id")
    @classmethod
    def _validate_scenario_id(cls, value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("scenario_id must be a non-empty string.")
        return value.strip()

    def to_dataclass(self) -> GroundTruthLabelDataclass:
        """Convert to dataclass representation."""
        return GroundTruthLabelDataclass(
            event_id=self.event_id,
            is_violation=self.is_violation,
            expected_label=ExpectedLabel(self.expected_label),
            expected_rule=(
                ExpectedRule(self.expected_rule) if self.expected_rule is not None else None
            ),
            missing_ops=list(self.missing_ops),
            scenario_id=self.scenario_id,
            seed=self.seed,
        )

    @classmethod
    def from_dataclass(cls, dc: GroundTruthLabelDataclass) -> GroundTruthLabel:
        return cls.model_validate(dc)


class JSONLLabelWriter:
    """Streams GroundTruthLabel records to a JSONL file or file-like object."""

    def __init__(self, target: str | Path | io.TextIOBase) -> None:
        self._target = target
        self._file: io.TextIOBase | None = None
        self._owns_file: bool = False

    def open(self) -> Self:
        if isinstance(self._target, (str, Path)):
            path = Path(self._target)
            path.parent.mkdir(parents=True, exist_ok=True)
            self._file = path.open("w", encoding="utf-8")
            self._owns_file = True
        else:
            self._file = self._target
            self._owns_file = False
        return self

    def close(self) -> None:
        if self._file is not None:
            self._file.flush()
            if self._owns_file:
                self._file.close()
            self._file = None

    def __enter__(self) -> Self:
        return self.open()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self.close()

    def write(self, label: GroundTruthLabel | GroundTruthLabelDataclass | dict[str, Any]) -> None:
        """Serialize and write one label record as a single JSON line."""
        if self._file is None:
            self.open()
        assert self._file is not None

        if isinstance(label, GroundTruthLabel):
            model = label
        elif isinstance(label, GroundTruthLabelDataclass):
            model = GroundTruthLabel.from_dataclass(label)
        elif isinstance(label, dict):
            model = GroundTruthLabel.model_validate(label)
        else:
            raise TypeError(f"Unsupported label type: {type(label)}")

        line = model.model_dump_json() + "\n"
        self._file.write(line)

    def write_all(
        self, labels: Iterable[GroundTruthLabel | GroundTruthLabelDataclass | dict[str, Any]]
    ) -> int:
        """Write all records from an iterable, returning total count written."""
        count = 0
        for item in labels:
            self.write(item)
            count += 1
        return count


class JSONLLabelReader:
    """Streams GroundTruthLabel records from a JSONL file or file-like object."""

    def __init__(self, source: str | Path | io.TextIOBase) -> None:
        self._source = source
        self._file: io.TextIOBase | None = None
        self._owns_file: bool = False

    def open(self) -> Self:
        if isinstance(self._source, (str, Path)):
            path = Path(self._source)
            if not path.is_file():
                raise FileNotFoundError(f"Label file not found: {path}")
            self._file = path.open("r", encoding="utf-8")
            self._owns_file = True
        else:
            self._file = self._source
            self._owns_file = False
        return self

    def close(self) -> None:
        if self._file is not None:
            if self._owns_file:
                self._file.close()
            self._file = None

    def __enter__(self) -> Self:
        return self.open()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self.close()

    def __iter__(self) -> Iterator[GroundTruthLabel]:
        """Yield GroundTruthLabel objects line-by-line from the JSONL source."""
        should_close = False
        if self._file is None:
            self.open()
            should_close = self._owns_file
        assert self._file is not None

        try:
            for line_no, raw in enumerate(self._file, start=1):
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                try:
                    payload = json.loads(line)
                    yield GroundTruthLabel.model_validate(payload)
                except Exception as exc:
                    raise ValueError(
                        f"Error parsing label record on line {line_no}: {exc}"
                    ) from exc
        finally:
            if should_close:
                self.close()

    def read_all(self) -> list[GroundTruthLabel]:
        """Read all labels into a list."""
        return list(self)
