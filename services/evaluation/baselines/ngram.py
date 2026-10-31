"""N-Gram sequence frequency baseline for API workflow anomaly detection.

Extracts sequential tuples of length n from clean training sessions,
builds frequency count tables with Laplace smoothing, and evaluates sequence anomaly scores.
"""

from __future__ import annotations

import collections
import math
from typing import Any

from services.evaluation.baselines.interfaces import (
    BaselineValidationError,
    BaseSequenceBaseline,
)


class NGramBaseline(BaseSequenceBaseline):
    """N-Gram sequence anomaly detection baseline.

    Extracts n-gram tuples from API operation sequences, maintains frequency
    tables across training sessions, and uses Laplace-smoothed conditional
    probabilities to compute sequence negative log-likelihood scores.
    """

    def __init__(self, n: int = 3, alpha: float = 0.01, **params: Any) -> None:
        """Initialize NGramBaseline.

        Args:
            n: Length of the n-gram tuples (default 3). Must be >= 1.
            alpha: Laplace smoothing parameter (default 0.01). Must be >= 0.0.
            **params: Additional hyperparameters for base class.

        Raises:
            BaselineValidationError: If n < 1 or alpha < 0.0 or non-finite.
            TypeError: If parameter types are invalid.
        """
        if isinstance(n, bool) or not isinstance(n, int):
            raise TypeError(f"n must be an int, got {type(n).__name__}")
        if n < 1:
            raise BaselineValidationError(f"n must be a positive integer >= 1, got {n}")

        if isinstance(alpha, bool) or not isinstance(alpha, (int, float)):
            raise TypeError(f"alpha must be a float or int, got {type(alpha).__name__}")
        if math.isnan(alpha) or math.isinf(alpha) or alpha < 0.0:
            raise BaselineValidationError(
                f"alpha must be a finite non-negative number, got {alpha}"
            )

        super().__init__(n=n, alpha=alpha, **params)
        self.n: int = n
        self.alpha: float = float(alpha)
        self._ngram_counts: collections.defaultdict[tuple[str, ...], int] = collections.defaultdict(
            int
        )
        self._prefix_counts: collections.defaultdict[tuple[str, ...], int] = (
            collections.defaultdict(int)
        )
        self._vocab: set[str] = set()
        self._unigram_counts: collections.defaultdict[str, int] = collections.defaultdict(int)

    # -------------------------------------------------------------------------
    # N-Gram Extraction & Accessors
    # -------------------------------------------------------------------------

    @staticmethod
    def extract_ngrams(sequence: list[str], n: int) -> list[tuple[str, ...]]:
        """Extract sequential n-gram tuples from an operation sequence.

        Args:
            sequence: Sequence of API operations.
            n: Length of n-gram tuples to extract.

        Returns:
            List of n-gram tuples. Returns an empty list if sequence length < n.

        Raises:
            BaselineValidationError: If n < 1.
        """
        if n < 1:
            raise BaselineValidationError(f"n must be a positive integer >= 1, got {n}")
        if len(sequence) < n:
            return []
        return [tuple(sequence[i : i + n]) for i in range(len(sequence) - n + 1)]

    def get_ngram_counts(self) -> dict[tuple[str, ...], int]:
        """Return a copy of the extracted n-gram frequency counts table.

        Returns:
            Dictionary mapping n-gram tuples to their observed integer frequencies.
        """
        return dict(self._ngram_counts)

    # -------------------------------------------------------------------------
    # Baseline Protocol Implementation
    # -------------------------------------------------------------------------

    def fit(self, clean_sequences: list[list[str]]) -> None:
        """Extract n-grams from clean per-session operation sequences and build frequency tables.

        Args:
            clean_sequences: List of clean API operation sequences.
        """
        self.validate_clean_sequences(clean_sequences)
        normalized_sequences = self.normalize_sequences(clean_sequences)

        self._ngram_counts = collections.defaultdict(int)
        self._prefix_counts = collections.defaultdict(int)
        self._vocab = set()
        self._unigram_counts = collections.defaultdict(int)

        for seq in normalized_sequences:
            for op in seq:
                self._vocab.add(op)
                self._unigram_counts[op] += 1

            ngrams = self.extract_ngrams(seq, self.n)
            for gram in ngrams:
                self._ngram_counts[gram] += 1
                if len(gram) > 1:
                    prefix = gram[:-1]
                    self._prefix_counts[prefix] += 1

        self._is_fitted = True

    def score_sequence(self, sequence: list[str]) -> float:
        """Score an observed sequence of API operations using negative log-probability.

        Computes length-normalized average negative log-probability across constituent n-grams
        using Laplace smoothing:
            P(w_i | w_{i-n+1}^{i-1}) = (count(w_{i-n+1}^i) + alpha) / (count(w_{i-n+1}^{i-1}) + alpha * |V|)

        Score is 0.0 for perfectly expected sequences (where every transition has count == prefix_count > 0)
        and scales positively with unseen or unexpected transitions.
        Unseen n-grams and zero counts are safely smoothed without triggering division-by-zero.

        Args:
            sequence: Sequence of API operations.

        Returns:
            Anomaly score as a non-negative float (0.0 for perfectly expected).
        """
        self.check_is_fitted()
        self.validate_sequence(sequence)
        normalized = self.normalize_sequence(sequence)

        if not normalized:
            return 0.0

        vocab_size = max(len(self._vocab), 1)
        effective_alpha = self.alpha if self.alpha > 0.0 else 1e-12

        # If sequence is shorter than n, evaluate with constituent unigrams
        if len(normalized) < self.n:
            total_unigrams = sum(self._unigram_counts.values())
            nll_sum = 0.0
            for op in normalized:
                count = self._unigram_counts.get(op, 0)
                if count > 0 and count == total_unigrams:
                    nll = 0.0
                else:
                    prob = (count + effective_alpha) / (
                        total_unigrams + effective_alpha * vocab_size
                    )
                    nll = -math.log(max(prob, 1e-12))
                nll_sum += nll
            return nll_sum / len(normalized)

        # Standard n-gram conditional evaluation
        ngrams = self.extract_ngrams(normalized, self.n)
        nll_sum = 0.0
        for gram in ngrams:
            count = self._ngram_counts.get(gram, 0)
            if self.n == 1:
                total_tokens = sum(self._ngram_counts.values())
                if count == total_tokens and count > 0:
                    nll = 0.0
                else:
                    prob = (count + effective_alpha) / (total_tokens + effective_alpha * vocab_size)
                    nll = -math.log(max(prob, 1e-12))
            else:
                prefix = gram[:-1]
                prefix_count = self._prefix_counts.get(prefix, 0)
                if count == prefix_count and count > 0:
                    # Perfectly expected transition (100% deterministic in clean data)
                    nll = 0.0
                else:
                    prob = (count + effective_alpha) / (prefix_count + effective_alpha * vocab_size)
                    nll = -math.log(max(prob, 1e-12))
            nll_sum += nll

        return nll_sum / len(ngrams)

    # -------------------------------------------------------------------------
    # State Serialization Overrides
    # -------------------------------------------------------------------------

    def _get_state(self) -> dict[str, Any]:
        """Export internal frequency tables to JSON-serializable state."""
        return {
            "ngram_counts": [
                {"ngram": list(gram), "count": count} for gram, count in self._ngram_counts.items()
            ],
            "prefix_counts": [
                {"prefix": list(p), "count": count} for p, count in self._prefix_counts.items()
            ],
            "vocab": sorted(self._vocab),
            "unigram_counts": dict(self._unigram_counts),
        }

    def _set_state(self, state: dict[str, Any]) -> None:
        """Restore internal frequency tables from state."""
        self._ngram_counts = collections.defaultdict(int)
        for item in state.get("ngram_counts", []):
            gram = tuple(item["ngram"])
            self._ngram_counts[gram] = item["count"]

        self._prefix_counts = collections.defaultdict(int)
        for item in state.get("prefix_counts", []):
            prefix = tuple(item["prefix"])
            self._prefix_counts[prefix] = item["count"]

        self._vocab = set(state.get("vocab", []))
        self._unigram_counts = collections.defaultdict(int, state.get("unigram_counts", {}))
