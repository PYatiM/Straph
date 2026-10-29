"""First-order Markov baseline for API workflow anomaly detection.

Builds a state-to-state conditional transition probability matrix M[op_a][op_b] = P(op_b | op_a)
using explicit start ('^') and terminal ('$') tokens.
"""

from __future__ import annotations

import collections
import copy
import math
from typing import Any

from services.evaluation.baselines.interfaces import (
    BaselineValidationError,
    BaseSequenceBaseline,
)

START_TOKEN: str = "^"
END_TOKEN: str = "$"


class FirstOrderMarkovBaseline(BaseSequenceBaseline):
    """First-order Markov sequence anomaly detection baseline.

    Constructs an empirical transition probability matrix M[op_a][op_b] = P(op_b | op_a)
    on clean API traces augmented with start ('^') and terminal ('$') tokens.
    All row probabilities sum to 1.0 +/- 1e-6.
    """

    def __init__(
        self,
        smoothing: float = 0.0,
        start_token: str = START_TOKEN,
        end_token: str = END_TOKEN,
        **params: Any,
    ) -> None:
        """Initialize FirstOrderMarkovBaseline.

        Args:
            smoothing: Smoothing parameter for unobserved transitions during scoring.
            start_token: Symbol representing the beginning of a trace (default '^').
            end_token: Symbol representing the end of a trace (default '$').
            **params: Additional parameters passed to BaseSequenceBaseline.

        Raises:
            BaselineValidationError: If tokens are empty, identical, or smoothing < 0.
            TypeError: If parameter types are invalid.
        """
        if not isinstance(start_token, str) or not start_token.strip():
            raise BaselineValidationError("start_token must be a non-empty string")
        if not isinstance(end_token, str) or not end_token.strip():
            raise BaselineValidationError("end_token must be a non-empty string")
        if start_token == end_token:
            raise BaselineValidationError("start_token and end_token must be distinct")

        if isinstance(smoothing, bool) or not isinstance(smoothing, (int, float)):
            raise TypeError(f"smoothing must be a float or int, got {type(smoothing).__name__}")
        if math.isnan(smoothing) or math.isinf(smoothing) or smoothing < 0.0:
            raise BaselineValidationError(
                f"smoothing must be a finite non-negative number, got {smoothing}"
            )

        super().__init__(
            smoothing=smoothing,
            start_token=start_token,
            end_token=end_token,
            **params,
        )
        self.smoothing: float = float(smoothing)
        self.start_token: str = start_token
        self.end_token: str = end_token

        self._transitions: dict[str, dict[str, float]] = {}
        self._transition_counts: dict[str, dict[str, int]] = {}
        self._vocab: set[str] = set()

    # -------------------------------------------------------------------------
    # Transition Accessors
    # -------------------------------------------------------------------------

    def get_transition_prob(self, from_op: str, to_op: str) -> float:
        """Return the conditional transition probability P(to_op | from_op).

        Args:
            from_op: Origin operation or start_token ('^').
            to_op: Destination operation or end_token ('$').

        Returns:
            Conditional transition probability in [0.0, 1.0].
            Returns 0.0 if the transition or origin state was unobserved.
        """
        self.check_is_fitted()
        if not isinstance(from_op, str) or not from_op.strip():
            raise BaselineValidationError("from_op must be a non-empty string")
        if not isinstance(to_op, str) or not to_op.strip():
            raise BaselineValidationError("to_op must be a non-empty string")

        norm_from = (
            from_op.strip()
            if from_op.strip() in (self.start_token, self.end_token)
            else self.parse_and_normalize_operation(from_op)
        )
        norm_to = (
            to_op.strip()
            if to_op.strip() in (self.start_token, self.end_token)
            else self.parse_and_normalize_operation(to_op)
        )

        row = self._transitions.get(norm_from, {})
        return row.get(norm_to, 0.0)

    def get_transition_matrix(self) -> dict[str, dict[str, float]]:
        """Return a deep copy of the first-order transition probability matrix M[op_a][op_b].

        Returns:
            Dictionary mapping origin operations to dictionaries of destination probabilities.
        """
        self.check_is_fitted()
        return copy.deepcopy(self._transitions)

    # -------------------------------------------------------------------------
    # Fitting and Scoring
    # -------------------------------------------------------------------------

    def fit(self, clean_sequences: list[list[str]]) -> None:
        """Calculate transition probability matrix M[op_a][op_b] = P(op_b | op_a).

        Augments each sequence with start ('^') and terminal ('$') tokens,
        counts state transitions, and computes row-normalized conditional probabilities.

        Args:
            clean_sequences: List of clean API operation sequences.
        """
        self.validate_clean_sequences(clean_sequences)
        normalized_sequences = self.normalize_sequences(clean_sequences)

        counts: collections.defaultdict[str, collections.defaultdict[str, int]] = (
            collections.defaultdict(lambda: collections.defaultdict(int))
        )
        self._vocab = set()

        for seq in normalized_sequences:
            for op in seq:
                self._vocab.add(op)

            # Augment sequence with start and terminal tokens
            augmented = [self.start_token] + seq + [self.end_token]
            for i in range(len(augmented) - 1):
                src = augmented[i]
                dst = augmented[i + 1]
                counts[src][dst] += 1

        # Calculate row-normalized conditional probability matrix M[src][dst]
        self._transitions = {}
        self._transition_counts = {}

        for src, dst_counts in counts.items():
            row_total = sum(dst_counts.values())
            self._transition_counts[src] = dict(dst_counts)
            if row_total > 0:
                self._transitions[src] = {
                    dst: count / row_total for dst, count in dst_counts.items()
                }
            else:
                self._transitions[src] = {}

        self._is_fitted = True

    def score_sequence(self, sequence: list[str]) -> float:
        """Score an observed sequence using average negative log-transition probabilities.

        Args:
            sequence: Sequence of API operations.

        Returns:
            Anomaly score as a float (0.0 for perfectly expected transitions).
        """
        self.check_is_fitted()
        self.validate_sequence(sequence)
        normalized = self.normalize_sequence(sequence)

        augmented = [self.start_token] + normalized + [self.end_token]
        total_transitions = len(augmented) - 1
        if total_transitions == 0:
            return 0.0

        vocab_size = max(len(self._vocab), 1) + 1  # includes end token
        effective_smoothing = self.smoothing if self.smoothing > 0.0 else 0.01

        nll_sum = 0.0
        for i in range(total_transitions):
            src = augmented[i]
            dst = augmented[i + 1]

            row = self._transitions.get(src, {})
            row_counts = self._transition_counts.get(src, {})
            prefix_total = sum(row_counts.values())

            prob = row.get(dst, 0.0)
            if prob == 1.0:
                # Deterministic expected transition contributes 0.0 penalty
                nll = 0.0
            elif prob > 0.0:
                nll = -math.log(prob)
            else:
                # Unseen transition: smoothed penalty avoiding division-by-zero
                smoothed_prob = effective_smoothing / (
                    prefix_total + effective_smoothing * vocab_size
                )
                nll = -math.log(max(smoothed_prob, 1e-12))

            nll_sum += nll

        return nll_sum / total_transitions

    # -------------------------------------------------------------------------
    # State Serialization
    # -------------------------------------------------------------------------

    def _get_state(self) -> dict[str, Any]:
        """Export internal model state for JSON/pickle serialization."""
        return {
            "transitions": copy.deepcopy(self._transitions),
            "transition_counts": copy.deepcopy(self._transition_counts),
            "vocab": sorted(self._vocab),
            "start_token": self.start_token,
            "end_token": self.end_token,
        }

    def _set_state(self, state: dict[str, Any]) -> None:
        """Restore internal model state from serialized dictionary."""
        self._transitions = copy.deepcopy(state.get("transitions", {}))
        self._transition_counts = copy.deepcopy(state.get("transition_counts", {}))
        self._vocab = set(state.get("vocab", []))
        self.start_token = state.get("start_token", self.start_token)
        self.end_token = state.get("end_token", self.end_token)
