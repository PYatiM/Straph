"""Unit tests for NGramBaseline frequency extraction and fitting.

Verifies:
- Extraction of sequential n-grams for n=2 (bigrams) and n=3 (trigrams) on toy checkout sequences.
- Frequency counting across single and multiple training sessions.
- Graceful handling of sequences shorter than n.
- get_ngram_counts() accessor immutability and correctness.
- Parameter validation for n and Laplace smoothing alpha.
- Anomaly scoring, predict threshold behavior, and serialization.
- Protocol conformance to SequenceBaselineProtocol.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from services.evaluation.baselines.interfaces import (
    BaselineNotFittedError,
    BaselineValidationError,
    SequenceBaselineProtocol,
)
from services.evaluation.baselines.ngram import NGramBaseline

# Toy checkout workflow sequences based on contracts/examples
TOY_CHECKOUT_STANDARD: list[str] = [
    "POST /cart/items",
    "POST /orders",
    "POST /orders/{id}/payment",
    "POST /orders/{id}/confirm",
]

TOY_CHECKOUT_CANCEL: list[str] = [
    "POST /cart/items",
    "POST /orders",
    "POST /orders/{id}/cancel",
]

TOY_CHECKOUT_SHORT_SINGLE: list[str] = [
    "POST /orders",
]

TOY_CHECKOUT_SHORT_EMPTY: list[str] = []


# =============================================================================
# Protocol Conformance
# =============================================================================


def test_ngram_conforms_to_sequence_baseline_protocol() -> None:
    """NGramBaseline must satisfy SequenceBaselineProtocol."""
    baseline = NGramBaseline()
    assert isinstance(baseline, SequenceBaselineProtocol)
    assert issubclass(NGramBaseline, SequenceBaselineProtocol)


# =============================================================================
# Parameter Validation
# =============================================================================


def test_default_initialization() -> None:
    """Default parameters should be n=3 and alpha=0.01."""
    baseline = NGramBaseline()
    assert baseline.n == 3
    assert baseline.alpha == 0.01
    assert not baseline.is_fitted
    assert baseline.get_ngram_counts() == {}


def test_custom_initialization() -> None:
    """Custom parameters should be set properly."""
    baseline = NGramBaseline(n=2, alpha=0.05)
    assert baseline.n == 2
    assert baseline.alpha == 0.05


@pytest.mark.parametrize("invalid_n", [0, -1, -5])
def test_invalid_n_value(invalid_n: int) -> None:
    """n < 1 should raise BaselineValidationError."""
    with pytest.raises(BaselineValidationError, match="n must be a positive integer"):
        NGramBaseline(n=invalid_n)


@pytest.mark.parametrize("invalid_n_type", ["3", 3.0, None, True, False])
def test_invalid_n_type(invalid_n_type: Any) -> None:
    """Non-int n should raise TypeError."""
    with pytest.raises(TypeError, match="n must be an int"):
        NGramBaseline(n=invalid_n_type)


@pytest.mark.parametrize("invalid_alpha", [-0.01, -1.0, float("nan"), float("inf"), float("-inf")])
def test_invalid_alpha_value(invalid_alpha: float) -> None:
    """Negative or non-finite alpha should raise BaselineValidationError."""
    with pytest.raises(BaselineValidationError, match="alpha must be a finite non-negative number"):
        NGramBaseline(alpha=invalid_alpha)


@pytest.mark.parametrize("invalid_alpha_type", ["0.01", None, True, False, [0.01]])
def test_invalid_alpha_type(invalid_alpha_type: Any) -> None:
    """Non-float/non-int alpha should raise TypeError."""
    with pytest.raises(TypeError, match="alpha must be a float or int"):
        NGramBaseline(alpha=invalid_alpha_type)


# =============================================================================
# extract_ngrams Helper Method
# =============================================================================


def test_extract_ngrams_basic() -> None:
    """extract_ngrams returns correct sequential tuples."""
    seq = ["A", "B", "C", "D"]

    # Bigrams (n=2)
    bigrams = NGramBaseline.extract_ngrams(seq, n=2)
    assert bigrams == [("A", "B"), ("B", "C"), ("C", "D")]

    # Trigrams (n=3)
    trigrams = NGramBaseline.extract_ngrams(seq, n=3)
    assert trigrams == [("A", "B", "C"), ("B", "C", "D")]

    # 4-grams (n=4)
    fourgrams = NGramBaseline.extract_ngrams(seq, n=4)
    assert fourgrams == [("A", "B", "C", "D")]


def test_extract_ngrams_shorter_than_n() -> None:
    """extract_ngrams returns empty list when sequence is shorter than n."""
    assert NGramBaseline.extract_ngrams(["A"], n=2) == []
    assert NGramBaseline.extract_ngrams(["A", "B"], n=3) == []
    assert NGramBaseline.extract_ngrams([], n=3) == []


def test_extract_ngrams_invalid_n() -> None:
    """extract_ngrams raises BaselineValidationError when n < 1."""
    with pytest.raises(BaselineValidationError, match="n must be a positive integer"):
        NGramBaseline.extract_ngrams(["A", "B"], n=0)


# =============================================================================
# Bigram (n=2) Extraction on Toy Checkout Sequences
# =============================================================================


def test_ngram_fit_n2_toy_checkout() -> None:
    """Fit n=2 extractor on toy checkout sequences and verify count table."""
    baseline = NGramBaseline(n=2)
    clean_sequences = [
        TOY_CHECKOUT_STANDARD,  # 4 ops -> 3 bigrams
        TOY_CHECKOUT_CANCEL,  # 3 ops -> 2 bigrams
    ]
    baseline.fit(clean_sequences)
    assert baseline.is_fitted

    counts = baseline.get_ngram_counts()

    expected_counts = {
        ("POST /cart/items", "POST /orders"): 2,
        ("POST /orders", "POST /orders/{id}/payment"): 1,
        ("POST /orders/{id}/payment", "POST /orders/{id}/confirm"): 1,
        ("POST /orders", "POST /orders/{id}/cancel"): 1,
    }

    assert counts == expected_counts
    assert sum(counts.values()) == 5


# =============================================================================
# Trigram (n=3) Extraction on Toy Checkout Sequences
# =============================================================================


def test_ngram_fit_n3_toy_checkout() -> None:
    """Fit n=3 extractor on toy checkout sequences and verify count table."""
    baseline = NGramBaseline(n=3)
    clean_sequences = [
        TOY_CHECKOUT_STANDARD,  # 4 ops -> 2 trigrams
        TOY_CHECKOUT_CANCEL,  # 3 ops -> 1 trigram
    ]
    baseline.fit(clean_sequences)
    assert baseline.is_fitted

    counts = baseline.get_ngram_counts()

    expected_counts = {
        ("POST /cart/items", "POST /orders", "POST /orders/{id}/payment"): 1,
        ("POST /orders", "POST /orders/{id}/payment", "POST /orders/{id}/confirm"): 1,
        ("POST /cart/items", "POST /orders", "POST /orders/{id}/cancel"): 1,
    }

    assert counts == expected_counts
    assert sum(counts.values()) == 3


# =============================================================================
# Sequences Shorter Than n
# =============================================================================


def test_ngram_fit_handles_sequences_shorter_than_n() -> None:
    """Extractor correctly handles sequences shorter than n without errors."""
    baseline = NGramBaseline(n=3)
    clean_sequences = [
        TOY_CHECKOUT_SHORT_SINGLE,  # length 1 < 3
        ["POST /orders", "POST /orders/{id}/cancel"],  # length 2 < 3
        TOY_CHECKOUT_SHORT_EMPTY,  # length 0 < 3
    ]
    baseline.fit(clean_sequences)
    assert baseline.is_fitted

    # No trigrams can be extracted from sequences of length < 3
    counts = baseline.get_ngram_counts()
    assert counts == {}


def test_ngram_fit_mixed_length_dataset() -> None:
    """Extractor accurately processes dataset with mixed sequence lengths."""
    baseline = NGramBaseline(n=2)
    clean_sequences = [
        TOY_CHECKOUT_SHORT_SINGLE,  # length 1 < 2 -> 0 bigrams
        TOY_CHECKOUT_CANCEL,  # length 3 >= 2 -> 2 bigrams
        TOY_CHECKOUT_SHORT_EMPTY,  # length 0 < 2 -> 0 bigrams
    ]
    baseline.fit(clean_sequences)

    counts = baseline.get_ngram_counts()
    expected = {
        ("POST /cart/items", "POST /orders"): 1,
        ("POST /orders", "POST /orders/{id}/cancel"): 1,
    }
    assert counts == expected


# =============================================================================
# Accessor Immutability
# =============================================================================


def test_get_ngram_counts_returns_defensive_copy() -> None:
    """Mutating the dictionary returned by get_ngram_counts does not affect internal state."""
    baseline = NGramBaseline(n=2)
    baseline.fit([["POST /cart/items", "POST /orders"]])

    counts = baseline.get_ngram_counts()
    counts[("FAKE", "OP")] = 999

    refreshed_counts = baseline.get_ngram_counts()
    assert ("FAKE", "OP") not in refreshed_counts


# =============================================================================
# Anomaly Scoring & Prediction
# =============================================================================


def test_unfitted_model_raises_on_scoring() -> None:
    """Scoring or predicting before fit raises BaselineNotFittedError."""
    baseline = NGramBaseline()
    with pytest.raises(BaselineNotFittedError):
        baseline.score_sequence(TOY_CHECKOUT_STANDARD)

    with pytest.raises(BaselineNotFittedError):
        baseline.predict(TOY_CHECKOUT_STANDARD, threshold=1.0)


def test_score_and_predict_toy_checkout() -> None:
    """Fitted model gives lower anomaly score to clean sequences than unseen sequences."""
    baseline = NGramBaseline(n=2, alpha=0.01)
    baseline.fit([TOY_CHECKOUT_STANDARD])

    clean_score = baseline.score_sequence(TOY_CHECKOUT_STANDARD)
    assert isinstance(clean_score, float)
    assert clean_score >= 0.0

    # Unseen anomalous transition
    anomalous_sequence = ["POST /orders", "DELETE /orders/{id}/force"]
    anomalous_score = baseline.score_sequence(anomalous_sequence)
    assert anomalous_score > clean_score

    # Predict with threshold
    threshold = (clean_score + anomalous_score) / 2.0
    assert not baseline.predict(TOY_CHECKOUT_STANDARD, threshold=threshold)
    assert baseline.predict(anomalous_sequence, threshold=threshold)


def test_score_short_sequence() -> None:
    """Model scores sequences shorter than n without crashing."""
    baseline = NGramBaseline(n=3, alpha=0.01)
    baseline.fit([TOY_CHECKOUT_STANDARD])

    # Sequence with length 1 < 3
    short_seq = ["POST /orders"]
    score = baseline.score_sequence(short_seq)
    assert isinstance(score, float)
    assert score >= 0.0


# =============================================================================
# Serialization
# =============================================================================


def test_save_and_load_json(tmp_path: Path) -> None:
    """NGramBaseline serializes and deserializes accurately with JSON."""
    baseline = NGramBaseline(n=2, alpha=0.05)
    baseline.fit([TOY_CHECKOUT_STANDARD, TOY_CHECKOUT_CANCEL])

    model_file = tmp_path / "ngram_model.json"
    baseline.save(model_file)
    assert model_file.is_file()

    loaded = NGramBaseline.load(model_file)
    assert isinstance(loaded, NGramBaseline)
    assert loaded.is_fitted
    assert loaded.n == 2
    assert loaded.alpha == 0.05
    assert loaded.get_ngram_counts() == baseline.get_ngram_counts()

    # Scores match before and after deserialization
    assert loaded.score_sequence(TOY_CHECKOUT_STANDARD) == baseline.score_sequence(
        TOY_CHECKOUT_STANDARD
    )


def test_save_and_load_pickle(tmp_path: Path) -> None:
    """NGramBaseline serializes and deserializes accurately with Pickle."""
    baseline = NGramBaseline(n=3, alpha=0.02)
    baseline.fit([TOY_CHECKOUT_STANDARD])

    model_file = tmp_path / "ngram_model.pkl"
    baseline.save(model_file)

    loaded = NGramBaseline.load(model_file)
    assert isinstance(loaded, NGramBaseline)
    assert loaded.is_fitted
    assert loaded.n == 3
    assert loaded.alpha == 0.02
    assert loaded.get_ngram_counts() == baseline.get_ngram_counts()
