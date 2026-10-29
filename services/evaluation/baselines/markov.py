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
        epsilon: float = 1e-4,
        smoothing: float | None = None,
        start_token: str = START_TOKEN,
        end_token: str = END_TOKEN,
        **params: Any,
    ) -> None:
        """Initialize FirstOrderMarkovBaseline.

        Args:
            epsilon: Smoothing floor parameter (default 1e-4).
            smoothing: Alias for epsilon for backward compatibility.
            start_token: Symbol representing the beginning of a trace (default '^').
            end_token: Symbol representing the end of a trace (default '$').
            **params: Additional parameters passed to BaseSequenceBaseline.

        Raises:
            BaselineValidationError: If tokens are empty, identical, or epsilon out of [0, 1].
            TypeError: If parameter types are invalid.
        """
        if not isinstance(start_token, str) or not start_token.strip():
            raise BaselineValidationError("start_token must be a non-empty string")
        if not isinstance(end_token, str) or not end_token.strip():
            raise BaselineValidationError("end_token must be a non-empty string")
        if start_token == end_token:
            raise BaselineValidationError("start_token and end_token must be distinct")

        effective_eps = smoothing if smoothing is not None else epsilon
        param_name = "smoothing" if smoothing is not None else "epsilon"
        if isinstance(effective_eps, bool) or not isinstance(effective_eps, (int, float)):
            raise TypeError(
                f"{param_name} must be a float or int, got {type(effective_eps).__name__}"
            )
        if (
            math.isnan(effective_eps)
            or math.isinf(effective_eps)
            or not (0.0 <= effective_eps <= 1.0)
        ):
            raise BaselineValidationError(
                f"{param_name} must be a finite non-negative number in [0.0, 1.0], got {effective_eps}"
            )

        super().__init__(
            epsilon=float(effective_eps),
            start_token=start_token,
            end_token=end_token,
            **params,
        )
        self.epsilon: float = float(effective_eps)
        self.smoothing: float = self.epsilon
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

    def score_sequence(self, sequence: list[str], epsilon: float | None = None) -> float:
        """Compute anomaly score as the average negative log transition probability across the chain.

        Formula:
            score = -1/K * sum_{i=1}^K ln( (1 - epsilon) * P(op_i | op_{i-1}) + epsilon * (1 / |V|) )

        where epsilon is a smoothing floor (default 1e-4) and |V| is the destination vocabulary size.

        Args:
            sequence: Sequence of API operations.
            epsilon: Optional smoothing floor override. Defaults to self.epsilon (1e-4).

        Returns:
            Anomaly score as a non-negative float.
        """
        self.check_is_fitted()
        self.validate_sequence(sequence)
        normalized = self.normalize_sequence(sequence)

        eps = self.epsilon if epsilon is None else float(epsilon)
        if isinstance(eps, bool) or not isinstance(eps, (int, float)):
            raise TypeError(f"epsilon must be a float or int, got {type(eps).__name__}")
        if math.isnan(eps) or math.isinf(eps) or not (0.0 <= eps <= 1.0):
            raise BaselineValidationError(f"epsilon must be in [0.0, 1.0], got {eps}")

        augmented = [self.start_token] + normalized + [self.end_token]
        total_transitions = len(augmented) - 1
        if total_transitions == 0:
            return 0.0

        vocab_size = max(len(self._vocab) + 1, 1)  # vocabulary of operations + end_token

        nll_sum = 0.0
        for i in range(total_transitions):
            src = augmented[i]
            dst = augmented[i + 1]

            row = self._transitions.get(src, {})
            p_transition = row.get(dst, 0.0)

            # Smoothed transition probability: (1 - eps) * P(op_i | op_{i-1}) + eps * (1 / |V|)
            smoothed_p = (1.0 - eps) * p_transition + eps * (1.0 / vocab_size)
            nll_sum += -math.log(max(smoothed_p, 1e-12))

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
            "epsilon": self.epsilon,
        }

    def _set_state(self, state: dict[str, Any]) -> None:
        """Restore internal model state from serialized dictionary."""
        self._transitions = copy.deepcopy(state.get("transitions", {}))
        self._transition_counts = copy.deepcopy(state.get("transition_counts", {}))
        self._vocab = set(state.get("vocab", []))
        self.start_token = state.get("start_token", self.start_token)
        self.end_token = state.get("end_token", self.end_token)
        self.epsilon = float(state.get("epsilon", self.epsilon))
        self.smoothing = self.epsilon
