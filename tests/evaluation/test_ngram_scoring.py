"""Unit tests for NGramBaseline sequence anomaly scoring.

Verifies:
- Legal sequences score lower than sequences skipping payment steps.
- Perfectly expected sequences score 0.0.
- Anomaly scores scale monotonically with unexpected transitions.
- Unseen n-grams and zero counts do not trigger division-by-zero.
- Exact match with Laplace smoothed probability formula.
- Decision threshold prediction correctly separates legal from abusive workflows.
"""

from __future__ import annotations

import math
from typing import Any

import pytest

from services.evaluation.baselines.interfaces import (
    BaselineNotFittedError,
    BaselineValidationError,
)
from services.evaluation.baselines.ngram import NGramBaseline

# Standard multi-step checkout workflow sequences (TC1 and TC2 equivalent)
LEGAL_CHECKOUT_SEQUENCE: list[str] = [
    "POST /cart/items",
    "POST /orders",
    "PUT /orders/{id}/address",
    "POST /orders/{id}/payment",
    "POST /payments/webhook",
    "POST /orders/{id}/fulfill",
    "POST /orders/{id}/ship",
]

SKIPPED_PAYMENT_SEQUENCE: list[str] = [
    "POST /cart/items",
    "POST /orders",
    "PUT /orders/{id}/address",
    "POST /orders/{id}/fulfill",  # skips payment and webhook
    "POST /orders/{id}/ship",
]

DOUBLE_SKIPPED_SEQUENCE: list[str] = [
    "POST /cart/items",
    "POST /orders",
    "POST /orders/{id}/ship",  # skips address, payment, webhook, fulfill
]


# =============================================================================
# Core Test: Legal Path vs Skipped Payment Path
# =============================================================================


@pytest.mark.parametrize("n", [2, 3])
def test_legal_sequence_scores_lower_than_skipped_payment(n: int) -> None:
    """A legal sequence must score strictly lower than a sequence skipping payment."""
    baseline = NGramBaseline(n=n, alpha=0.01)
    baseline.fit([LEGAL_CHECKOUT_SEQUENCE])

    legal_score = baseline.score_sequence(LEGAL_CHECKOUT_SEQUENCE)
    skipped_payment_score = baseline.score_sequence(SKIPPED_PAYMENT_SEQUENCE)

    # Perfectly expected sequence should have 0.0 anomaly score
    assert legal_score == 0.0

    # Skipped payment sequence must exhibit significant positive anomaly score
    assert skipped_payment_score > 0.0
    assert legal_score < skipped_payment_score


def test_predict_classifies_skipped_payment_as_anomaly() -> None:
    """Predict correctly labels skipped payment as anomalous based on threshold."""
    baseline = NGramBaseline(n=2, alpha=0.01)
    baseline.fit([LEGAL_CHECKOUT_SEQUENCE])

    legal_score = baseline.score_sequence(LEGAL_CHECKOUT_SEQUENCE)
    skipped_score = baseline.score_sequence(SKIPPED_PAYMENT_SEQUENCE)

    # Decision threshold placed between normal and abnormal scores
    threshold = (legal_score + skipped_score) / 2.0

    assert not baseline.predict(LEGAL_CHECKOUT_SEQUENCE, threshold=threshold)
    assert baseline.predict(SKIPPED_PAYMENT_SEQUENCE, threshold=threshold)


# =============================================================================
# Monotonic Scaling with Unexpected Transitions
# =============================================================================


def test_anomaly_score_scales_monotonically_with_unexpected_transitions() -> None:
    """Anomaly score scales monotonically as more transitions deviate from expected behavior."""
    baseline = NGramBaseline(n=2, alpha=0.01)
    baseline.fit([LEGAL_CHECKOUT_SEQUENCE])

    # 0 unexpected transitions
    score_0 = baseline.score_sequence(LEGAL_CHECKOUT_SEQUENCE)

    # 1 unexpected transition (address -> fulfill)
    score_1 = baseline.score_sequence(SKIPPED_PAYMENT_SEQUENCE)

    # 2 unexpected transitions (orders -> ship)
    score_2 = baseline.score_sequence(DOUBLE_SKIPPED_SEQUENCE)

    # Completely illegal sequence with foreign operations
    foreign_sequence = [
        "POST /orders",
        "DELETE /admin/reset",
        "POST /orders/{id}/ship",
    ]
    score_foreign = baseline.score_sequence(foreign_sequence)

    assert score_0 == 0.0
    assert score_0 < score_1
    assert score_1 < score_2
    assert score_2 < score_foreign


# =============================================================================
# Division-by-Zero Safety & Extreme Inputs
# =============================================================================


@pytest.mark.parametrize("alpha", [0.01, 0.1, 1.0, 0.0])
def test_unseen_ngrams_do_not_trigger_division_by_zero(alpha: float) -> None:
    """Unseen n-grams and zero counts must not trigger ZeroDivisionError or domain errors."""
    baseline = NGramBaseline(n=2, alpha=alpha)
    baseline.fit([LEGAL_CHECKOUT_SEQUENCE])

    # Case 1: Unseen transition with known prefix
    unseen_transition = ["POST /orders", "POST /unknown/action"]
    score_1 = baseline.score_sequence(unseen_transition)
    assert math.isfinite(score_1)
    assert score_1 > 0.0

    # Case 2: Unseen transition with unseen prefix
    completely_unseen = ["DELETE /unknown", "PUT /fake"]
    score_2 = baseline.score_sequence(completely_unseen)
    assert math.isfinite(score_2)
    assert score_2 > 0.0

    # Case 3: Mixed sequence with repeated unseen operations
    mixed = ["POST /cart/items", "DELETE /fake", "DELETE /fake2"]
    score_3 = baseline.score_sequence(mixed)
    assert math.isfinite(score_3)
    assert score_3 > 0.0


def test_zero_alpha_handled_safely() -> None:
    """alpha=0.0 must be safely handled using internal epsilon fallback."""
    baseline = NGramBaseline(n=2, alpha=0.0)
    baseline.fit([["POST /cart/items", "POST /orders"]])

    # Perfectly expected sequence still scores 0.0
    assert baseline.score_sequence(["POST /cart/items", "POST /orders"]) == 0.0

    # Unseen sequence does not crash with division by zero or log(0)
    score_unseen = baseline.score_sequence(["POST /orders", "DELETE /unseen"])
    assert math.isfinite(score_unseen)
    assert score_unseen > 0.0


# =============================================================================
# Exact Laplace Formula Verification
# =============================================================================


def test_laplace_formula_exact_value() -> None:
    """Verify calculated score matches manual evaluation of the Laplace smoothing equation.

    Formula:
        P(w_i | prefix) = (count(gram) + alpha) / (count(prefix) + alpha * |V|)
        score = average(-ln(P))
    """
    alpha = 0.05
    n = 2
    clean_sequences = [
        ["POST /orders", "POST /orders/{id}/payment"],
        ["POST /orders", "POST /orders/{id}/cancel"],
    ]
    baseline = NGramBaseline(n=n, alpha=alpha)
    baseline.fit(clean_sequences)

    # Vocabulary is {'POST /orders', 'POST /orders/{id}/payment', 'POST /orders/{id}/cancel'}
    vocab_size = 3
    # From 'POST /orders', payment was chosen 1/2 times and cancel 1/2 times
    prefix_count = 2

    # Query 1: payment transition (seen 1 time out of 2)
    seq_payment = ["POST /orders", "POST /orders/{id}/payment"]
    count_payment = 1
    expected_prob_payment = (count_payment + alpha) / (prefix_count + alpha * vocab_size)
    expected_score_payment = -math.log(expected_prob_payment)

    actual_score_payment = baseline.score_sequence(seq_payment)
    assert math.isclose(actual_score_payment, expected_score_payment, rel_tol=1e-6)

    # Query 2: unseen transition from same prefix (seen 0 times)
    seq_unseen = ["POST /orders", "DELETE /orders/{id}"]
    count_unseen = 0
    expected_prob_unseen = (count_unseen + alpha) / (prefix_count + alpha * vocab_size)
    expected_score_unseen = -math.log(expected_prob_unseen)

    actual_score_unseen = baseline.score_sequence(seq_unseen)
    assert math.isclose(actual_score_unseen, expected_score_unseen, rel_tol=1e-6)

    # Monotonicity check
    assert actual_score_payment < actual_score_unseen


# =============================================================================
# Edge Cases & Validation
# =============================================================================


def test_score_empty_sequence() -> None:
    """Empty sequence returns 0.0 anomaly score."""
    baseline = NGramBaseline()
    baseline.fit([LEGAL_CHECKOUT_SEQUENCE])
    assert baseline.score_sequence([]) == 0.0


def test_score_unfitted_raises() -> None:
    """Calling score_sequence on unfitted model raises BaselineNotFittedError."""
    baseline = NGramBaseline()
    with pytest.raises(BaselineNotFittedError):
        baseline.score_sequence(LEGAL_CHECKOUT_SEQUENCE)


@pytest.mark.parametrize(
    "invalid_input",
    [
        "not a list",
        None,
        123,
        [1, 2],
        ["POST /orders", ""],
    ],
)
def test_score_invalid_sequence_raises(invalid_input: Any) -> None:
    """Invalid sequence structure or elements raise TypeError or BaselineValidationError."""
    baseline = NGramBaseline()
    baseline.fit([LEGAL_CHECKOUT_SEQUENCE])
    with pytest.raises((TypeError, BaselineValidationError)):
        baseline.score_sequence(invalid_input)

