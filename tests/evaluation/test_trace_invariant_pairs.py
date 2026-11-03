"""Unit tests for TraceInvariantBaseline transition pair extraction and invariant checking.

Verifies:
- Accurate extraction of unique adjacent operation pairs (op_t, op_{t+1}) on checkout sequences.
- allowed_pairs correctly stores distinct transitions across linear, branching, and repeated traces.
- Graceful handling of sequences shorter than 2 operations.
- is_allowed_pair and get_allowed_pairs accessors behavior and defensive copying.
- Anomaly scoring and threshold prediction on legal vs skipped payment workflows.
- JSON and Pickle model serialization fidelity.
- Protocol conformance to SequenceBaselineProtocol.
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
from services.evaluation.baselines.trace_invariant import TraceInvariantBaseline

# Standard checkout workflows
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

CHECKOUT_FLOW_SHORT_SINGLE: list[str] = [
    "POST /orders",
]

CHECKOUT_FLOW_EMPTY: list[str] = []


# =============================================================================
# Protocol Conformance
# =============================================================================


def test_trace_invariant_conforms_to_protocol() -> None:
    """TraceInvariantBaseline must satisfy SequenceBaselineProtocol."""
    baseline = TraceInvariantBaseline()
    assert isinstance(baseline, SequenceBaselineProtocol)
    assert issubclass(TraceInvariantBaseline, SequenceBaselineProtocol)


# =============================================================================
# extract_pairs Helper Function
# =============================================================================


def test_extract_pairs_various_lengths() -> None:
    """extract_pairs yields adjacent sequential pairs or empty list for short sequences."""
    seq_4 = ["A", "B", "C", "D"]
    assert TraceInvariantBaseline.extract_pairs(seq_4) == [
        ("A", "B"),
        ("B", "C"),
        ("C", "D"),
    ]

    seq_2 = ["A", "B"]
    assert TraceInvariantBaseline.extract_pairs(seq_2) == [("A", "B")]

    # Sequences shorter than 2
    assert TraceInvariantBaseline.extract_pairs(["A"]) == []
    assert TraceInvariantBaseline.extract_pairs([]) == []


# =============================================================================
# Pair Extraction on Toy Checkout Sequences
# =============================================================================


def test_allowed_pairs_single_checkout_flow() -> None:
    """Extract adjacent pairs from a single linear checkout trace."""
    baseline = TraceInvariantBaseline()
    baseline.fit([CHECKOUT_FLOW_PAY])

    expected_pairs = {
        ("POST /cart/items", "POST /orders"),
        ("POST /orders", "POST /orders/{id}/payment"),
        ("POST /orders/{id}/payment", "POST /orders/{id}/confirm"),
    }

    assert baseline.allowed_pairs == expected_pairs
    assert len(baseline.allowed_pairs) == 3


def test_allowed_pairs_branching_checkout_flows() -> None:
    """Extract adjacent pairs across branching workflows (payment vs cancel)."""
    baseline = TraceInvariantBaseline()
    baseline.fit([CHECKOUT_FLOW_PAY, CHECKOUT_FLOW_CANCEL])

    expected_pairs = {
        ("POST /cart/items", "POST /orders"),
        ("POST /orders", "POST /orders/{id}/payment"),
        ("POST /orders/{id}/payment", "POST /orders/{id}/confirm"),
        ("POST /orders", "POST /orders/{id}/cancel"),
    }

    assert baseline.allowed_pairs == expected_pairs
    assert len(baseline.allowed_pairs) == 4


def test_allowed_pairs_deduplicates_repeated_traces() -> None:
    """Repeated execution of identical transitions maintains unique set elements."""
    baseline = TraceInvariantBaseline()
    # 5 identical pay flows + 5 cancel flows
    traces = [CHECKOUT_FLOW_PAY] * 5 + [CHECKOUT_FLOW_CANCEL] * 5
    baseline.fit(traces)

    assert len(baseline.allowed_pairs) == 4


# =============================================================================
# Handling Sequences Shorter than 2
# =============================================================================


def test_fit_handles_short_sequences() -> None:
    """fit handles traces with length 0 or 1 without error."""
    baseline = TraceInvariantBaseline()
    baseline.fit([CHECKOUT_FLOW_SHORT_SINGLE, CHECKOUT_FLOW_EMPTY])

    assert baseline.is_fitted
    assert baseline.allowed_pairs == set()


def test_fit_mixed_length_dataset() -> None:
    """fit extracts pairs only from multi-step sequences in mixed datasets."""
    baseline = TraceInvariantBaseline()
    traces = [
        CHECKOUT_FLOW_SHORT_SINGLE,  # len 1 -> 0 pairs
        CHECKOUT_FLOW_CANCEL,  # len 3 -> 2 pairs
        CHECKOUT_FLOW_EMPTY,  # len 0 -> 0 pairs
    ]
    baseline.fit(traces)

    expected = {
        ("POST /cart/items", "POST /orders"),
        ("POST /orders", "POST /orders/{id}/cancel"),
    }
    assert baseline.allowed_pairs == expected


# =============================================================================
# is_allowed_pair and get_allowed_pairs Accessors
# =============================================================================


def test_is_allowed_pair_accuracy() -> None:
    """is_allowed_pair accurately verifies allowed vs disallowed transitions."""
    baseline = TraceInvariantBaseline()
    baseline.fit([CHECKOUT_FLOW_PAY])

    # Valid adjacent transitions
    assert baseline.is_allowed_pair("POST /cart/items", "POST /orders")
    assert baseline.is_allowed_pair("POST /orders", "POST /orders/{id}/payment")
    assert baseline.is_allowed_pair("POST /orders/{id}/payment", "POST /orders/{id}/confirm")

    # Invalid / skipped transitions
    assert not baseline.is_allowed_pair("POST /orders", "POST /orders/{id}/confirm")
    assert not baseline.is_allowed_pair("POST /orders/{id}/confirm", "POST /orders")
    assert not baseline.is_allowed_pair("POST /cart/items", "DELETE /cart")


def test_is_allowed_pair_normalizes_input() -> None:
    """is_allowed_pair normalizes input strings (whitespace, lowercase method)."""
    baseline = TraceInvariantBaseline()
    baseline.fit([CHECKOUT_FLOW_PAY])

    # Normalization of method and path
    assert baseline.is_allowed_pair(" post   /cart/items/ ", "post /orders/")


def test_get_allowed_pairs_returns_defensive_copy() -> None:
    """Mutating the set from get_allowed_pairs does not affect model state."""
    baseline = TraceInvariantBaseline()
    baseline.fit([CHECKOUT_FLOW_PAY])

    pairs_copy = baseline.get_allowed_pairs()
    pairs_copy.add(("FAKE", "PAIR"))

    assert ("FAKE", "PAIR") not in baseline.allowed_pairs
    assert ("FAKE", "PAIR") not in baseline.get_allowed_pairs()


def test_accessors_unfitted_raise() -> None:
    """Calling accessors before fit raises BaselineNotFittedError."""
    baseline = TraceInvariantBaseline()

    with pytest.raises(BaselineNotFittedError):
        baseline.get_allowed_pairs()

    with pytest.raises(BaselineNotFittedError):
        baseline.is_allowed_pair("POST /cart/items", "POST /orders")


@pytest.mark.parametrize("invalid_arg", ["", "   "])
def test_is_allowed_pair_invalid_args_raise(invalid_arg: str) -> None:
    """Empty or blank arguments raise BaselineValidationError."""
    baseline = TraceInvariantBaseline()
    baseline.fit([CHECKOUT_FLOW_PAY])

    with pytest.raises(BaselineValidationError):
        baseline.is_allowed_pair(invalid_arg, "POST /orders")

    with pytest.raises(BaselineValidationError):
        baseline.is_allowed_pair("POST /orders", invalid_arg)


# =============================================================================
# Scoring and Prediction
# =============================================================================


def test_score_and_predict_checkout_sequences() -> None:
    """Legal sequence scores 0.0 while skipped payment sequence produces positive score."""
    baseline = TraceInvariantBaseline()
    baseline.fit([CHECKOUT_FLOW_PAY])

    # Legal sequence has 0 invalid pairs
    legal_score = baseline.score_sequence(CHECKOUT_FLOW_PAY)
    assert legal_score == 0.0

    # Skipped payment sequence: 3 transitions total, 1 invalid (orders -> confirm)
    skipped_payment_seq = [
        "POST /cart/items",
        "POST /orders",
        "POST /orders/{id}/confirm",
    ]
    skipped_score = baseline.score_sequence(skipped_payment_seq)
    assert math.isclose(skipped_score, 1.0 / 2.0, rel_tol=1e-5)

    # Completely reversed sequence: all 3 transitions invalid
    reversed_seq = list(reversed(CHECKOUT_FLOW_PAY))
    reversed_score = baseline.score_sequence(reversed_seq)
    assert reversed_score == 1.0

    # Prediction threshold
    assert not baseline.predict(CHECKOUT_FLOW_PAY, threshold=0.1)
    assert baseline.predict(skipped_payment_seq, threshold=0.1)
    assert baseline.predict(reversed_seq, threshold=0.1)


def test_score_short_sequences() -> None:
    """Sequences with length < 2 return 0.0."""
    baseline = TraceInvariantBaseline()
    baseline.fit([CHECKOUT_FLOW_PAY])

    assert baseline.score_sequence([]) == 0.0
    assert baseline.score_sequence(["POST /orders"]) == 0.0


# =============================================================================
# Serialization
# =============================================================================


def test_save_and_load_json(tmp_path: Path) -> None:
    """TraceInvariantBaseline serializes and deserializes accurately with JSON."""
    baseline = TraceInvariantBaseline()
    baseline.fit([CHECKOUT_FLOW_PAY, CHECKOUT_FLOW_CANCEL])

    model_file = tmp_path / "trace_invariant.json"
    baseline.save(model_file)
    assert model_file.is_file()

    loaded = TraceInvariantBaseline.load(model_file)
    assert loaded.is_fitted
    assert loaded.allowed_pairs == baseline.allowed_pairs
    assert loaded.score_sequence(CHECKOUT_FLOW_PAY) == 0.0


def test_save_and_load_pickle(tmp_path: Path) -> None:
    """TraceInvariantBaseline serializes and deserializes accurately with Pickle."""
    baseline = TraceInvariantBaseline()
    baseline.fit([CHECKOUT_FLOW_PAY])

    model_file = tmp_path / "trace_invariant.pkl"
    baseline.save(model_file)

    loaded = TraceInvariantBaseline.load(model_file)
    assert loaded.is_fitted
    assert loaded.allowed_pairs == baseline.allowed_pairs
