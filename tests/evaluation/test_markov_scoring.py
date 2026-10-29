"""Unit tests for FirstOrderMarkovBaseline sequence anomaly scoring.

Verifies:
- Reversing a sequence produces a higher anomaly score than the forward legal sequence.
- Skipped workflows produce significantly higher anomaly scores than legal sequences.
- Anomaly scores scale monotonically with workflow deviations (legal < skipped < reversed).
- Computed scores match the smoothed log-likelihood formula:
  score = -1/K * sum_{i=1}^K ln( (1 - eps) * P(op_i | op_{i-1}) + eps * (1 / |V|) )
- Smoothing floor (epsilon) behaves correctly across default (1e-4) and custom values.
- Decision threshold prediction correctly separates legal from anomalous workflows.
"""

from __future__ import annotations

import math
from typing import Any

import pytest

from services.evaluation.baselines.interfaces import (
    BaselineNotFittedError,
    BaselineValidationError,
)
from services.evaluation.baselines.markov import FirstOrderMarkovBaseline

# 7-step full checkout workflow (TC1 legal path)
LEGAL_CHECKOUT_FLOW: list[str] = [
    "POST /cart/items",
    "POST /orders",
    "PUT /orders/{id}/address",
    "POST /orders/{id}/payment",
    "POST /payments/webhook",
    "POST /orders/{id}/fulfill",
    "POST /orders/{id}/ship",
]

# Reversed sequence
REVERSED_CHECKOUT_FLOW: list[str] = list(reversed(LEGAL_CHECKOUT_FLOW))

# Skipped payment workflow (TC2 path)
SKIPPED_PAYMENT_FLOW: list[str] = [
    "POST /cart/items",
    "POST /orders",
    "PUT /orders/{id}/address",
    "POST /orders/{id}/fulfill",  # skips payment and webhook
    "POST /orders/{id}/ship",
]

# Multiple-step skipped workflow
DOUBLE_SKIPPED_FLOW: list[str] = [
    "POST /cart/items",
    "POST /orders",
    "POST /orders/{id}/ship",  # skips address, payment, webhook, fulfill
]


# =============================================================================
# Core Test: Reversing a Sequence Produces Higher Anomaly Score
# =============================================================================


def test_reversing_sequence_produces_higher_anomaly_score() -> None:
    """Reversing a workflow produces a significantly higher anomaly score than the forward sequence."""
    baseline = FirstOrderMarkovBaseline(epsilon=1e-4)
    baseline.fit([LEGAL_CHECKOUT_FLOW])

    forward_score = baseline.score_sequence(LEGAL_CHECKOUT_FLOW)
    reversed_score = baseline.score_sequence(REVERSED_CHECKOUT_FLOW)

    # Forward legal sequence follows observed transitions: smoothed NLL near zero
    assert forward_score < 0.001

    # Reversed sequence visits unobserved reverse transitions: smoothed NLL is very high
    assert reversed_score > 5.0
    assert forward_score < reversed_score


def test_reversing_toy_sequence_produces_higher_anomaly_score() -> None:
    """Reversing a 4-step toy checkout sequence produces a higher anomaly score."""
    toy_legal = [
        "POST /cart/items",
        "POST /orders",
        "POST /orders/{id}/payment",
        "POST /orders/{id}/confirm",
    ]
    toy_reversed = list(reversed(toy_legal))

    baseline = FirstOrderMarkovBaseline(epsilon=1e-4)
    baseline.fit([toy_legal])

    legal_score = baseline.score_sequence(toy_legal)
    reversed_score = baseline.score_sequence(toy_reversed)

    assert legal_score < 0.001
    assert reversed_score > 5.0
    assert legal_score < reversed_score


# =============================================================================
# Skipped Workflows & Monotonic Ordering
# =============================================================================


def test_skipped_workflows_produce_high_anomaly_scores() -> None:
    """Workflows that skip critical authorization/payment steps produce high anomaly scores."""
    baseline = FirstOrderMarkovBaseline(epsilon=1e-4)
    baseline.fit([LEGAL_CHECKOUT_FLOW])

    legal_score = baseline.score_sequence(LEGAL_CHECKOUT_FLOW)
    skipped_payment_score = baseline.score_sequence(SKIPPED_PAYMENT_FLOW)

    assert legal_score < 0.001
    assert skipped_payment_score > 1.0
    assert legal_score < skipped_payment_score


def test_monotonic_scaling_with_deviations() -> None:
    """Anomaly scores scale monotonically: legal < 1 skip < multiple skips < fully reversed."""
    baseline = FirstOrderMarkovBaseline(epsilon=1e-4)
    baseline.fit([LEGAL_CHECKOUT_FLOW])

    score_legal = baseline.score_sequence(LEGAL_CHECKOUT_FLOW)
    score_skip_1 = baseline.score_sequence(SKIPPED_PAYMENT_FLOW)
    score_skip_2 = baseline.score_sequence(DOUBLE_SKIPPED_FLOW)
    score_reversed = baseline.score_sequence(REVERSED_CHECKOUT_FLOW)

    assert score_legal < score_skip_1
    assert score_skip_1 < score_skip_2
    assert score_skip_2 < score_reversed


# =============================================================================
# Mathematical Formula Exact Match
# =============================================================================


def test_score_matches_smoothed_log_likelihood_formula() -> None:
    """Score matches manual evaluation of the negative log smoothed transition formula.

    Formula:
        score = -1/K * sum_{i=1}^K ln( (1 - eps) * P(op_i | op_{i-1}) + eps * (1 / |V|) )
    """
    clean_trace = ["POST /orders", "POST /orders/{id}/payment"]
    eps = 0.01  # use 1% floor for easy manual inspection
    baseline = FirstOrderMarkovBaseline(epsilon=eps)
    baseline.fit([clean_trace])

    # |V| = 2 unique ops + 1 end_token ('$') = 3
    vocab_size = 3

    # Query: exact clean sequence
    # Transitions:
    # 1. '^' -> 'POST /orders' (P = 1.0)
    # 2. 'POST /orders' -> 'POST /orders/{id}/payment' (P = 1.0)
    # 3. 'POST /orders/{id}/payment' -> '$' (P = 1.0)
    p_smoothed = (1.0 - eps) * 1.0 + eps * (1.0 / vocab_size)
    expected_nll = -math.log(p_smoothed)
    actual_score = baseline.score_sequence(clean_trace)

    assert math.isclose(actual_score, expected_nll, rel_tol=1e-6)

    # Query: reversed sequence where all 3 transitions have P = 0.0
    reversed_trace = ["POST /orders/{id}/payment", "POST /orders"]
    p_unseen_smoothed = (1.0 - eps) * 0.0 + eps * (1.0 / vocab_size)
    expected_unseen_nll = -math.log(p_unseen_smoothed)
    actual_unseen_score = baseline.score_sequence(reversed_trace)

    assert math.isclose(actual_unseen_score, expected_unseen_nll, rel_tol=1e-6)
    assert actual_score < actual_unseen_score


# =============================================================================
# Epsilon Smoothing Floor Behavior
# =============================================================================


@pytest.mark.parametrize("eps", [1e-3, 1e-4, 1e-5])
def test_epsilon_parameter_scaling(eps: float) -> None:
    """Smaller epsilon increases the penalty for unseen/reversed transitions."""
    baseline = FirstOrderMarkovBaseline(epsilon=eps)
    baseline.fit([LEGAL_CHECKOUT_FLOW])

    legal_score = baseline.score_sequence(LEGAL_CHECKOUT_FLOW)
    reversed_score = baseline.score_sequence(REVERSED_CHECKOUT_FLOW)

    assert math.isfinite(legal_score)
    assert math.isfinite(reversed_score)
    assert legal_score < reversed_score


def test_score_sequence_epsilon_override() -> None:
    """score_sequence supports an optional epsilon override argument."""
    baseline = FirstOrderMarkovBaseline(epsilon=1e-4)
    baseline.fit([LEGAL_CHECKOUT_FLOW])

    score_default = baseline.score_sequence(REVERSED_CHECKOUT_FLOW)
    score_higher_eps = baseline.score_sequence(REVERSED_CHECKOUT_FLOW, epsilon=1e-2)

    # Higher epsilon (softer penalty) yields a lower score for unseen transitions
    assert score_higher_eps < score_default


# =============================================================================
# Predict Method Integration
# =============================================================================


def test_predict_distinguishes_legal_from_reversed() -> None:
    """Predict method labels legal workflow as normal and reversed workflow as anomalous."""
    baseline = FirstOrderMarkovBaseline(epsilon=1e-4)
    baseline.fit([LEGAL_CHECKOUT_FLOW])

    legal_score = baseline.score_sequence(LEGAL_CHECKOUT_FLOW)
    reversed_score = baseline.score_sequence(REVERSED_CHECKOUT_FLOW)
    threshold = (legal_score + reversed_score) / 2.0

    assert not baseline.predict(LEGAL_CHECKOUT_FLOW, threshold=threshold)
    assert baseline.predict(REVERSED_CHECKOUT_FLOW, threshold=threshold)


# =============================================================================
# Edge Cases & Validation
# =============================================================================


def test_score_unfitted_raises() -> None:
    """Calling score_sequence before fit raises BaselineNotFittedError."""
    baseline = FirstOrderMarkovBaseline()
    with pytest.raises(BaselineNotFittedError):
        baseline.score_sequence(LEGAL_CHECKOUT_FLOW)


@pytest.mark.parametrize("invalid_eps", [-0.1, 1.5, float("nan"), float("inf")])
def test_score_invalid_epsilon_override(invalid_eps: float) -> None:
    """Out-of-range or NaN epsilon override raises BaselineValidationError."""
    baseline = FirstOrderMarkovBaseline()
    baseline.fit([LEGAL_CHECKOUT_FLOW])
    with pytest.raises(BaselineValidationError):
        baseline.score_sequence(LEGAL_CHECKOUT_FLOW, epsilon=invalid_eps)


@pytest.mark.parametrize(
    "invalid_input",
    ["not a list", None, 123, [1, 2], ["POST /orders", ""]],
)
def test_score_invalid_sequence_structure(invalid_input: Any) -> None:
    """Invalid sequence structure raises TypeError or BaselineValidationError."""
    baseline = FirstOrderMarkovBaseline()
    baseline.fit([LEGAL_CHECKOUT_FLOW])
    with pytest.raises((TypeError, BaselineValidationError)):
        baseline.score_sequence(invalid_input)

