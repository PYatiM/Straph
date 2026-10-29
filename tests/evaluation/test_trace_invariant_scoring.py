"""Unit tests for TraceInvariantBaseline violation prediction and explanation.

Verifies:
- Discovery of single_use_ops from clean training sessions (operations occurring at most once per object).
- predict(sequence) returns True for unallowed adjacent pairs and repeated single-use operations.
- explain_violation(sequence) returns the exact offending transition pair or repeated operation.
- Validates checkout skip (unseen adjacent pair) and double-refund (replayed one-shot op) test cases.
- Legal multi-step workflows evaluate to False with None as explanation.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from services.evaluation.baselines.interfaces import (
    BaselineNotFittedError,
    SequenceBaselineProtocol,
)
from services.evaluation.baselines.trace_invariant import TraceInvariantBaseline

# Comprehensive multi-step clean workflows
CLEAN_CHECKOUT_DELIVER_REFUND: list[str] = [
    "POST /cart/items",
    "POST /orders",
    "PUT /orders/{id}/address",
    "POST /orders/{id}/payment",
    "POST /payments/webhook",
    "POST /orders/{id}/fulfill",
    "POST /orders/{id}/ship",
    "POST /orders/{id}/deliver",
    "POST /orders/{id}/refund",
]

CLEAN_CHECKOUT_MULTI_CART: list[str] = [
    "POST /cart/items",
    "POST /cart/items",  # multi-use operation (added 2 items to cart)
    "POST /orders",
    "POST /orders/{id}/cancel",
]

# TC2 checkout skip path: skips payment and webhook
CHECKOUT_SKIP_PAYMENT_FLOW: list[str] = [
    "POST /cart/items",
    "POST /orders",
    "PUT /orders/{id}/address",
    "POST /orders/{id}/fulfill",  # unallowed transition directly from address
    "POST /orders/{id}/ship",
]

# TC4 double-refund path: legal flow followed by replayed refund
DOUBLE_REFUND_FLOW: list[str] = [
    "POST /cart/items",
    "POST /orders",
    "PUT /orders/{id}/address",
    "POST /orders/{id}/payment",
    "POST /payments/webhook",
    "POST /orders/{id}/fulfill",
    "POST /orders/{id}/ship",
    "POST /orders/{id}/deliver",
    "POST /orders/{id}/refund",
    "POST /orders/{id}/refund",  # replay of single-use refund
]


# =============================================================================
# Discovery of Single-Use Operations
# =============================================================================


def test_single_use_ops_discovery() -> None:
    """Operations occurring at most once across all clean traces are identified as single-use."""
    baseline = TraceInvariantBaseline()
    baseline.fit([CLEAN_CHECKOUT_DELIVER_REFUND, CLEAN_CHECKOUT_MULTI_CART])

    # Operations that only ever appeared once per session
    assert "POST /orders" in baseline.single_use_ops
    assert "PUT /orders/{id}/address" in baseline.single_use_ops
    assert "POST /orders/{id}/payment" in baseline.single_use_ops
    assert "POST /orders/{id}/refund" in baseline.single_use_ops
    assert "POST /orders/{id}/cancel" in baseline.single_use_ops

    # Multi-use operation: 'POST /cart/items' appeared twice in CLEAN_CHECKOUT_MULTI_CART
    assert "POST /cart/items" not in baseline.single_use_ops


# =============================================================================
# Checkout Skip Case (Offending Adjacent Pair)
# =============================================================================


def test_checkout_skip_case_flagged_and_explained() -> None:
    """Checkout skip (skipping payment) is flagged with the exact offending transition pair."""
    baseline = TraceInvariantBaseline()
    baseline.fit([CLEAN_CHECKOUT_DELIVER_REFUND, CLEAN_CHECKOUT_MULTI_CART])

    # predict() flags the checkout skip sequence as a violation
    assert baseline.predict(CHECKOUT_SKIP_PAYMENT_FLOW) is True

    # explain_violation() returns the exact unallowed transition pair
    offending_pair = baseline.explain_violation(CHECKOUT_SKIP_PAYMENT_FLOW)
    assert offending_pair == (
        "PUT /orders/{id}/address",
        "POST /orders/{id}/fulfill",
    )

    # score_sequence produces a positive violation score
    assert baseline.score_sequence(CHECKOUT_SKIP_PAYMENT_FLOW) > 0.0


# =============================================================================
# Double-Refund Case (Repeated Single-Use Operation)
# =============================================================================


def test_double_refund_case_flagged_and_explained() -> None:
    """Double-refund replay is flagged with the exact repeated single-use operation."""
    baseline = TraceInvariantBaseline()
    baseline.fit([CLEAN_CHECKOUT_DELIVER_REFUND, CLEAN_CHECKOUT_MULTI_CART])

    # predict() flags the double-refund sequence as a violation
    assert baseline.predict(DOUBLE_REFUND_FLOW) is True

    # explain_violation() returns the exact repeated operation
    offending_op = baseline.explain_violation(DOUBLE_REFUND_FLOW)
    assert offending_op == "POST /orders/{id}/refund"

    # score_sequence produces a positive violation score
    assert baseline.score_sequence(DOUBLE_REFUND_FLOW) > 0.0


def test_double_refund_separated_by_intermediate_operation() -> None:
    """Repeated single-use operation is flagged even when separated by another operation."""
    baseline = TraceInvariantBaseline()
    baseline.fit([CLEAN_CHECKOUT_DELIVER_REFUND])

    # Refund repeated after an intervening operation
    separated_double_refund = [
        "POST /orders",
        "POST /orders/{id}/payment",
        "POST /orders/{id}/fulfill",
        "POST /orders/{id}/ship",
        "POST /orders/{id}/deliver",
        "POST /orders/{id}/refund",
        "GET /orders/{id}/status",
        "POST /orders/{id}/refund",  # second refund
    ]

    assert baseline.predict(separated_double_refund) is True
    offending = baseline.explain_violation(separated_double_refund)
    assert offending in (
        "POST /orders/{id}/refund",
        ("POST /orders/{id}/refund", "GET /orders/{id}/status"),
    )


# =============================================================================
# Legal Workflow Case (No Violation)
# =============================================================================


def test_legal_flow_has_no_violation() -> None:
    """Legal workflow passes with predict=False, explanation=None, score=0.0."""
    baseline = TraceInvariantBaseline()
    baseline.fit([CLEAN_CHECKOUT_DELIVER_REFUND, CLEAN_CHECKOUT_MULTI_CART])

    assert baseline.predict(CLEAN_CHECKOUT_DELIVER_REFUND) is False
    assert baseline.explain_violation(CLEAN_CHECKOUT_DELIVER_REFUND) is None
    assert baseline.score_sequence(CLEAN_CHECKOUT_DELIVER_REFUND) == 0.0


def test_allowed_multi_use_repetition_not_flagged() -> None:
    """Repeating a multi-use operation (e.g. adding items to cart) is not flagged."""
    baseline = TraceInvariantBaseline()
    baseline.fit([CLEAN_CHECKOUT_DELIVER_REFUND, CLEAN_CHECKOUT_MULTI_CART])

    # Sequence with 3 cart additions (cart/items is multi-use in training)
    three_cart_items = [
        "POST /cart/items",
        "POST /cart/items",
        "POST /orders",
        "POST /orders/{id}/cancel",
    ]

    assert baseline.predict(three_cart_items) is False
    assert baseline.explain_violation(three_cart_items) is None


# =============================================================================
# Protocol Conformance & Serialization
# =============================================================================


def test_protocol_conformance() -> None:
    """TraceInvariantBaseline satisfies SequenceBaselineProtocol."""
    baseline = TraceInvariantBaseline()
    assert isinstance(baseline, SequenceBaselineProtocol)


def test_serialization_preserves_single_use_ops(tmp_path: Path) -> None:
    """Saving and loading via JSON/Pickle preserves single_use_ops and explanation."""
    baseline = TraceInvariantBaseline()
    baseline.fit([CLEAN_CHECKOUT_DELIVER_REFUND, CLEAN_CHECKOUT_MULTI_CART])

    json_path = tmp_path / "model.json"
    baseline.save(json_path)

    loaded = TraceInvariantBaseline.load(json_path)
    assert loaded.is_fitted
    assert loaded.single_use_ops == baseline.single_use_ops
    assert loaded.allowed_pairs == baseline.allowed_pairs

    # Verify predictions and explanations match
    assert loaded.predict(CHECKOUT_SKIP_PAYMENT_FLOW) is True
    assert loaded.explain_violation(CHECKOUT_SKIP_PAYMENT_FLOW) == (
        "PUT /orders/{id}/address",
        "POST /orders/{id}/fulfill",
    )
    assert loaded.explain_violation(DOUBLE_REFUND_FLOW) == "POST /orders/{id}/refund"


def test_explain_violation_unfitted_raises() -> None:
    """Calling explain_violation on unfitted model raises BaselineNotFittedError."""
    baseline = TraceInvariantBaseline()
    with pytest.raises(BaselineNotFittedError):
        baseline.explain_violation(CHECKOUT_SKIP_PAYMENT_FLOW)
