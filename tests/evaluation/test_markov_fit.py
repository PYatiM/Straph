"""Unit tests for FirstOrderMarkovBaseline transition matrix fitting.

Verifies:
- All row probabilities in the transition matrix sum to 1.0 +/- 1e-6.
- Explicit start ('^') and terminal ('$') tokens capture session boundaries.
- Accurate conditional transition probabilities on single and branching workflows.
- get_transition_prob accessor accuracy for observed and unobserved transitions.
- Model serialization and SequenceBaselineProtocol conformance.
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from services.evaluation.baselines.interfaces import (
    BaselineNotFittedError,
    BaselineValidationError,
    SequenceBaselineProtocol,
)
from services.evaluation.baselines.markov import (
    END_TOKEN,
    START_TOKEN,
    FirstOrderMarkovBaseline,
)

# Standard toy checkout sequences
CHECKOUT_FLOW_PAY: list[str] = [
    "POST /cart/items",
    "POST /orders",
    "POST /orders/{id}/payment",
    "POST /orders/{id}/confirm",
]

CHECKOUT_FLOW_CANCEL: list[str] = [
    "POST /cart/items",
    "POST /orders",
    "POST /orders/{id}/cancel",
]

CHECKOUT_FLOW_DIRECT_BUY: list[str] = [
    "POST /orders",
    "POST /orders/{id}/payment",
]


# =============================================================================
# Protocol Conformance
# =============================================================================


def test_markov_conforms_to_protocol() -> None:
    """FirstOrderMarkovBaseline must satisfy SequenceBaselineProtocol."""
    baseline = FirstOrderMarkovBaseline()
    assert isinstance(baseline, SequenceBaselineProtocol)
    assert issubclass(FirstOrderMarkovBaseline, SequenceBaselineProtocol)


# =============================================================================
# Row Probability Sums (1.0 +/- 1e-6)
# =============================================================================


def test_row_probabilities_sum_to_one_single_flow() -> None:
    """Every row in the transition matrix must sum to 1.0 +/- 1e-6 on a linear flow."""
    baseline = FirstOrderMarkovBaseline()
    baseline.fit([CHECKOUT_FLOW_PAY])

    matrix = baseline.get_transition_matrix()
    assert len(matrix) > 0

    for src_op, row in matrix.items():
        row_sum = sum(row.values())
        assert math.isclose(row_sum, 1.0, abs_tol=1e-6), (
            f"Row for '{src_op}' sums to {row_sum}, expected 1.0"
        )


def test_row_probabilities_sum_to_one_branching_workflow() -> None:
    """Every row in the transition matrix must sum to 1.0 +/- 1e-6 with branching paths."""
    baseline = FirstOrderMarkovBaseline()
    # 7 sessions pay, 3 sessions cancel
    clean_sequences = [CHECKOUT_FLOW_PAY] * 7 + [CHECKOUT_FLOW_CANCEL] * 3
    baseline.fit(clean_sequences)

    matrix = baseline.get_transition_matrix()
    assert len(matrix) > 0

    for src_op, row in matrix.items():
        row_sum = sum(row.values())
        assert math.isclose(row_sum, 1.0, abs_tol=1e-6), (
            f"Row for '{src_op}' sums to {row_sum}, expected 1.0"
        )


def test_row_probabilities_sum_to_one_multiple_entrypoints() -> None:
    """Every row sums to 1.0 +/- 1e-6 when sessions have distinct start and terminal points."""
    baseline = FirstOrderMarkovBaseline()
    clean_sequences = [
        CHECKOUT_FLOW_PAY,
        CHECKOUT_FLOW_CANCEL,
        CHECKOUT_FLOW_DIRECT_BUY,
        ["POST /orders"],
        [],
    ]
    baseline.fit(clean_sequences)

    matrix = baseline.get_transition_matrix()
    for src_op, row in matrix.items():
        row_sum = sum(row.values())
        assert math.isclose(row_sum, 1.0, abs_tol=1e-6), (
            f"Row for '{src_op}' sums to {row_sum}, expected 1.0"
        )


# =============================================================================
# Start ('^') and Terminal ('$') Token Capture
# =============================================================================


def test_start_and_terminal_tokens_captured() -> None:
    """Transition matrix contains start ('^') transitions and transitions to terminal ('$')."""
    baseline = FirstOrderMarkovBaseline()
    baseline.fit([CHECKOUT_FLOW_PAY])

    # Start token '^' transitions to the first operation
    p_start_to_cart = baseline.get_transition_prob(START_TOKEN, "POST /cart/items")
    assert math.isclose(p_start_to_cart, 1.0, abs_tol=1e-6)

    # Last operation transitions to terminal token '$'
    p_confirm_to_end = baseline.get_transition_prob("POST /orders/{id}/confirm", END_TOKEN)
    assert math.isclose(p_confirm_to_end, 1.0, abs_tol=1e-6)


def test_branching_start_tokens() -> None:
    """Multiple entry operations correctly divide the start token '^' row probability."""
    baseline = FirstOrderMarkovBaseline()
    # 3 sessions start with /cart/items, 1 starts directly with /orders
    clean_sequences = [
        ["POST /cart/items", "POST /orders"],
        ["POST /cart/items", "POST /orders"],
        ["POST /cart/items", "POST /orders"],
        ["POST /orders"],
    ]
    baseline.fit(clean_sequences)

    p_cart = baseline.get_transition_prob(START_TOKEN, "POST /cart/items")
    p_orders = baseline.get_transition_prob(START_TOKEN, "POST /orders")

    assert math.isclose(p_cart, 0.75, abs_tol=1e-6)
    assert math.isclose(p_orders, 0.25, abs_tol=1e-6)
    assert math.isclose(p_cart + p_orders, 1.0, abs_tol=1e-6)


# =============================================================================
# Exact Conditional Probabilities
# =============================================================================


def test_exact_conditional_probabilities_branching() -> None:
    """State-to-state conditional probabilities match empirical trace ratios."""
    baseline = FirstOrderMarkovBaseline()
    # POST /orders branches: 6 to payment, 4 to cancel
    clean_sequences = [CHECKOUT_FLOW_PAY] * 6 + [CHECKOUT_FLOW_CANCEL] * 4
    baseline.fit(clean_sequences)

    p_pay = baseline.get_transition_prob("POST /orders", "POST /orders/{id}/payment")
    p_cancel = baseline.get_transition_prob("POST /orders", "POST /orders/{id}/cancel")

    assert math.isclose(p_pay, 0.6, abs_tol=1e-6)
    assert math.isclose(p_cancel, 0.4, abs_tol=1e-6)


# =============================================================================
# get_transition_prob Accessor Tests
# =============================================================================


def test_get_transition_prob_unobserved_transitions() -> None:
    """get_transition_prob returns 0.0 for unobserved transitions or unseen states."""
    baseline = FirstOrderMarkovBaseline()
    baseline.fit([CHECKOUT_FLOW_PAY])

    # Unobserved destination from known state
    assert baseline.get_transition_prob("POST /orders", "DELETE /orders/{id}") == 0.0

    # Completely unobserved origin state
    assert baseline.get_transition_prob("DELETE /nonexistent", "POST /orders") == 0.0


def test_get_transition_prob_unfitted_raises() -> None:
    """Calling get_transition_prob before fit raises BaselineNotFittedError."""
    baseline = FirstOrderMarkovBaseline()
    with pytest.raises(BaselineNotFittedError):
        baseline.get_transition_prob(START_TOKEN, "POST /orders")


@pytest.mark.parametrize("invalid_arg", ["", "   "])
def test_get_transition_prob_invalid_args_raise(invalid_arg: str) -> None:
    """Empty or whitespace-only operation arguments raise BaselineValidationError."""
    baseline = FirstOrderMarkovBaseline()
    baseline.fit([CHECKOUT_FLOW_PAY])

    with pytest.raises(BaselineValidationError):
        baseline.get_transition_prob(invalid_arg, "POST /orders")

    with pytest.raises(BaselineValidationError):
        baseline.get_transition_prob("POST /orders", invalid_arg)


# =============================================================================
# Parameter Validation & Custom Tokens
# =============================================================================


def test_custom_boundary_tokens() -> None:
    """Custom start and terminal tokens are respected throughout fitting."""
    baseline = FirstOrderMarkovBaseline(start_token="[START]", end_token="[END]")
    baseline.fit([["POST /orders"]])

    assert baseline.get_transition_prob("[START]", "POST /orders") == 1.0
    assert baseline.get_transition_prob("POST /orders", "[END]") == 1.0


def test_identical_start_and_end_tokens_rejected() -> None:
    """Identical start and end tokens raise BaselineValidationError."""
    with pytest.raises(BaselineValidationError, match="must be distinct"):
        FirstOrderMarkovBaseline(start_token="X", end_token="X")


@pytest.mark.parametrize("invalid_smoothing", [-0.5, float("nan"), float("inf")])
def test_invalid_smoothing_rejected(invalid_smoothing: float) -> None:
    """Invalid smoothing values raise BaselineValidationError."""
    with pytest.raises(BaselineValidationError, match="finite non-negative"):
        FirstOrderMarkovBaseline(smoothing=invalid_smoothing)


# =============================================================================
# Scoring & Serialization
# =============================================================================


def test_markov_scoring_distinguishes_legal_from_anomaly() -> None:
    """Legal workflow scores significantly lower than out-of-order/skipped sequence."""
    baseline = FirstOrderMarkovBaseline()
    baseline.fit([CHECKOUT_FLOW_PAY])

    legal_score = baseline.score_sequence(CHECKOUT_FLOW_PAY)
    assert legal_score < 0.001

    # Skipped payment sequence
    skipped_seq = [
        "POST /cart/items",
        "POST /orders",
        "POST /orders/{id}/confirm",  # skipped payment
    ]
    skipped_score = baseline.score_sequence(skipped_seq)
    assert skipped_score > legal_score


def test_markov_save_and_load_json(tmp_path: Path) -> None:
    """Markov model state and transition matrix are preserved through JSON serialization."""
    baseline = FirstOrderMarkovBaseline()
    baseline.fit([CHECKOUT_FLOW_PAY, CHECKOUT_FLOW_CANCEL])

    model_path = tmp_path / "markov.json"
    baseline.save(model_path)

    loaded = FirstOrderMarkovBaseline.load(model_path)
    assert loaded.is_fitted
    assert loaded.get_transition_matrix() == baseline.get_transition_matrix()
    assert loaded.score_sequence(CHECKOUT_FLOW_PAY) == baseline.score_sequence(CHECKOUT_FLOW_PAY)


def test_markov_save_and_load_pickle(tmp_path: Path) -> None:
    """Markov model state is preserved through pickle serialization."""
    baseline = FirstOrderMarkovBaseline()
    baseline.fit([CHECKOUT_FLOW_PAY])

    model_path = tmp_path / "markov.pkl"
    baseline.save(model_path)

    loaded = FirstOrderMarkovBaseline.load(model_path)
    assert loaded.is_fitted
    assert loaded.get_transition_matrix() == baseline.get_transition_matrix()
