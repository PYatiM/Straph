"""Constants and enums for the detector service."""

from enum import Enum


class ViolationLabel(str, Enum):
    """Labels for workflow and authorization violations."""

    SKIPPED = "SKIPPED"
    OUT_OF_ORDER = "OUT_OF_ORDER"
    REPLAYED = "REPLAYED"
    IDENTITY_MISMATCH = "IDENTITY_MISMATCH"
    UNMODELED = "UNMODELED"


class RuleId(str, Enum):
    """Rule identifiers for the deterministic detection core."""

    R0 = "R0"
    R1 = "R1"
    R2 = "R2"
    R3 = "R3"
    R4 = "R4"


class SeverityLevel(str, Enum):
    """Severity levels for emitted alerts."""

    ATTEMPTED = "attempted"
    ACCEPTED = "accepted"


LEGAL_ADVANCE: str = "LEGAL_ADVANCE"

RULE_ORDER: list[str] = [
    "R4",
    "R0",
    "LEGAL_ADVANCE",
    "R1",
    "R2",
    "R3",
]
