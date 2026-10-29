"""Trace invariant baseline for API workflow anomaly detection.

Extracts unique adjacent operation pairs (op_t, op_{t+1}) from clean training traces
and detects multi-step business workflow violations based on invariant pair checking.
"""

from __future__ import annotations

from typing import Any

from services.evaluation.baselines.interfaces import (
    BaselineValidationError,
    BaseSequenceBaseline,
)


class TraceInvariantBaseline(BaseSequenceBaseline):
    """Trace invariant sequence anomaly detection baseline.

    Identifies allowed adjacent operation transitions from clean training streams,
    storing them in self.allowed_pairs. Sequences containing adjacent pairs not present
    in the allowed set are scored as invariant violations.
    """

    def __init__(self, **params: Any) -> None:
        """Initialize TraceInvariantBaseline."""
        super().__init__(**params)
        self.allowed_pairs: set[tuple[str, str]] = set()
        self._vocab: set[str] = set()

    # -------------------------------------------------------------------------
    # Pair Extraction & Accessors
    # -------------------------------------------------------------------------

    @staticmethod
    def extract_pairs(sequence: list[str]) -> list[tuple[str, str]]:
        """Extract adjacent operation pairs (op_t, op_{t+1}) from a sequence.

        Args:
            sequence: Sequence of API operations.

        Returns:
            List of adjacent operation tuples. Returns empty list if length < 2.
        """
        if len(sequence) < 2:
            return []
        return [(sequence[i], sequence[i + 1]) for i in range(len(sequence) - 1)]

    def get_allowed_pairs(self) -> set[tuple[str, str]]:
        """Return a copy of the allowed adjacent operation pairs set."""
        self.check_is_fitted()
        return set(self.allowed_pairs)

    def is_allowed_pair(self, from_op: str, to_op: str) -> bool:
        """Check whether a transition between two operations is an allowed invariant pair.

        Args:
            from_op: Origin operation string.
            to_op: Destination operation string.

        Returns:
            True if (from_op, to_op) was observed in clean training data, False otherwise.
        """
        self.check_is_fitted()
        if not isinstance(from_op, str) or not from_op.strip():
            raise BaselineValidationError("from_op must be a non-empty string")
        if not isinstance(to_op, str) or not to_op.strip():
            raise BaselineValidationError("to_op must be a non-empty string")

        norm_from = self.parse_and_normalize_operation(from_op)
        norm_to = self.parse_and_normalize_operation(to_op)
        return (norm_from, norm_to) in self.allowed_pairs

    # -------------------------------------------------------------------------
    # Fitting and Scoring
    # -------------------------------------------------------------------------

    def fit(self, clean_sequences: list[list[str]]) -> None:
        """Extract all unique adjacent operation pairs observed in clean traces.

        Stores valid transitions in self.allowed_pairs.

        Args:
            clean_sequences: List of clean API operation sequences.
        """
        self.validate_clean_sequences(clean_sequences)
        normalized_sequences = self.normalize_sequences(clean_sequences)

        self.allowed_pairs = set()
        self._vocab = set()

        for seq in normalized_sequences:
            for op in seq:
                self._vocab.add(op)

            pairs = self.extract_pairs(seq)
            for pair in pairs:
                self.allowed_pairs.add(pair)

        self._is_fitted = True

    def score_sequence(self, sequence: list[str]) -> float:
        """Compute anomaly score as fraction of adjacent pairs violating trace invariants.

        Args:
            sequence: Sequence of API operations.

        Returns:
            Anomaly score in [0.0, 1.0]. 0.0 indicates all transitions are valid allowed pairs.
        """
        self.check_is_fitted()
        self.validate_sequence(sequence)
        normalized = self.normalize_sequence(sequence)

        if len(normalized) < 2:
            return 0.0

        total_pairs = len(normalized) - 1
        violations = sum(
            1
            for i in range(total_pairs)
            if (normalized[i], normalized[i + 1]) not in self.allowed_pairs
        )
        return violations / total_pairs

    # -------------------------------------------------------------------------
    # State Serialization Overrides
    # -------------------------------------------------------------------------

    def _get_state(self) -> dict[str, Any]:
        """Export allowed pairs to JSON-serializable list format."""
        return {
            "allowed_pairs": [list(pair) for pair in sorted(self.allowed_pairs)],
            "vocab": sorted(self._vocab),
        }

    def _set_state(self, state: dict[str, Any]) -> None:
        """Restore allowed pairs from serialized state."""
        self.allowed_pairs = {(item[0], item[1]) for item in state.get("allowed_pairs", [])}
        self._vocab = set(state.get("vocab", []))
