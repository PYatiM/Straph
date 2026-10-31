"""Trace invariant baseline for API workflow anomaly detection.

Extracts unique adjacent operation pairs (op_t, op_{t+1}) from clean training traces
and detects multi-step business workflow violations based on invariant pair checking.
"""

from __future__ import annotations

import collections
from typing import Any

from services.evaluation.baselines.interfaces import (
    BaselineValidationError,
    BaseSequenceBaseline,
)


class TraceInvariantBaseline(BaseSequenceBaseline):
    """Trace invariant sequence anomaly detection baseline.

    Identifies allowed adjacent operation transitions from clean training streams,
    storing them in self.allowed_pairs. Also discovers operations that occur at most
    once per session in self.single_use_ops.
    Flags unseen adjacent pairs and repeated single-use operations with exact failure reasons.
    """

    def __init__(self, **params: Any) -> None:
        """Initialize TraceInvariantBaseline."""
        super().__init__(**params)
        self.allowed_pairs: set[tuple[str, str]] = set()
        self.single_use_ops: set[str] = set()
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
    # Fitting, Invariant Checking, and Scoring
    # -------------------------------------------------------------------------

    def fit(self, clean_sequences: list[list[str]]) -> None:
        """Extract unique adjacent operation pairs and discover single-use operations.

        Stores valid transitions in self.allowed_pairs and operations that occur at most
        once per session across all clean training sessions in self.single_use_ops.

        Args:
            clean_sequences: List of clean API operation sequences.
        """
        self.validate_clean_sequences(clean_sequences)
        normalized_sequences = self.normalize_sequences(clean_sequences)

        self.allowed_pairs = set()
        self._vocab = set()
        all_clean_ops: set[str] = set()
        multi_use_ops: set[str] = set()

        for seq in normalized_sequences:
            op_counts = collections.Counter(seq)
            for op, count in op_counts.items():
                self._vocab.add(op)
                all_clean_ops.add(op)
                if count > 1:
                    multi_use_ops.add(op)

            pairs = self.extract_pairs(seq)
            for pair in pairs:
                self.allowed_pairs.add(pair)

        # Operations that occur at most once per object across all clean training sessions
        self.single_use_ops = all_clean_ops - multi_use_ops
        self._is_fitted = True

    def predict(self, sequence: list[str], threshold: float | None = None) -> bool:
        """Predict whether a sequence violates trace invariants.

        Args:
            sequence: Sequence of API operations.
            threshold: Optional decision threshold. If None, evaluates invariant logic directly.

        Returns:
            True (violation) if any adjacent pair (seq[i], seq[i+1]) is not in allowed_pairs
            OR if any operation in single_use_ops occurs > 1 times in sequence.
            False if the sequence is valid.
        """
        self.check_is_fitted()
        self.validate_sequence(sequence)
        normalized = self.normalize_sequence(sequence)

        if threshold is not None:
            self.validate_threshold(threshold)
            if threshold > 0.0:
                return self.score_sequence(sequence) >= threshold

        # Check for disallowed adjacent transition pairs
        for i in range(len(normalized) - 1):
            if (normalized[i], normalized[i + 1]) not in self.allowed_pairs:
                return True

        # Check for repeated single-use operations
        counts = collections.Counter(normalized)
        for op in self.single_use_ops:
            if counts[op] > 1:
                return True

        return False

    def explain_violation(self, sequence: list[str]) -> tuple[str, str] | str | None:
        """Return the exact offending transition pair or repeated operation.

        Args:
            sequence: Sequence of API operations.

        Returns:
            tuple[str, str]: If an adjacent pair (seq[i], seq[i+1]) is not in allowed_pairs.
            str: If an operation in single_use_ops occurs > 1 times.
            None: If the sequence has no invariant violations.
        """
        self.check_is_fitted()
        self.validate_sequence(sequence)
        normalized = self.normalize_sequence(sequence)

        # Check for repeated single-use operations
        counts = collections.Counter(normalized)
        for op in normalized:
            if op in self.single_use_ops and counts[op] > 1:
                return op

        # Check for disallowed adjacent transition pairs
        for i in range(len(normalized) - 1):
            pair = (normalized[i], normalized[i + 1])
            if pair not in self.allowed_pairs:
                return pair

        return None

    def score_sequence(self, sequence: list[str]) -> float:
        """Compute anomaly score based on invariant violations.

        Args:
            sequence: Sequence of API operations.

        Returns:
            Anomaly score in [0.0, 1.0]. 0.0 indicates all transitions are valid allowed pairs
            and no single-use operation is repeated.
        """
        self.check_is_fitted()
        self.validate_sequence(sequence)
        normalized = self.normalize_sequence(sequence)

        if len(normalized) < 2 and not any(op in self.single_use_ops for op in normalized):
            return 0.0

        total_pairs = max(len(normalized) - 1, 1)
        pair_violations = sum(
            1
            for i in range(len(normalized) - 1)
            if (normalized[i], normalized[i + 1]) not in self.allowed_pairs
        )

        counts = collections.Counter(normalized)
        single_use_violations = sum(counts[op] - 1 for op in self.single_use_ops if counts[op] > 1)

        total_violations = pair_violations + single_use_violations
        return min(total_violations / total_pairs, 1.0)

    # -------------------------------------------------------------------------
    # State Serialization Overrides
    # -------------------------------------------------------------------------

    def _get_state(self) -> dict[str, Any]:
        """Export model state to JSON-serializable list format."""
        return {
            "allowed_pairs": [list(pair) for pair in sorted(self.allowed_pairs)],
            "single_use_ops": sorted(self.single_use_ops),
            "vocab": sorted(self._vocab),
        }

    def _set_state(self, state: dict[str, Any]) -> None:
        """Restore model state from serialized dictionary."""
        self.allowed_pairs = {(item[0], item[1]) for item in state.get("allowed_pairs", [])}
        self.single_use_ops = set(state.get("single_use_ops", []))
        self._vocab = set(state.get("vocab", []))
