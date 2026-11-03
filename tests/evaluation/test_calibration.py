"""Unit tests for empirical threshold calibration on held-out clean validation sessions.

Verifies:
- Exact quantile matching across synthetic uniform, Gaussian, and tied score distributions.
- Calibrated thresholds satisfy false-positive budgets without exceeding target limits.
- End-to-end integration with NGramBaseline without lookahead bias.
- Robust handling of edge cases (0.0 budget, 1.0 budget, ties at maximum score).
- Input validation on budgets, datasets, and baseline protocol conformance.
"""

from __future__ import annotations

import math
import random
from typing import Any

import pytest

from services.evaluation.baselines.calibration import (
    calibrate_threshold,
    select_threshold_from_scores,
)
from services.evaluation.baselines.interfaces import (
    BaselineValidationError,
    SequenceBaselineProtocol,
)
from services.evaluation.baselines.ngram import NGramBaseline

# =============================================================================
# Synthetic Score Distributions - Exact Quantile Matching
# =============================================================================


def test_uniform_distribution_exact_quantiles() -> None:
    """Linearly spaced synthetic scores produce exact quantile matching."""
    # 1000 discrete scores from 0.001 to 1.000: scores[i] = (i + 1) / 1000
    n = 1000
    scores = [(i + 1) / n for i in range(n)]

    # 1% FP budget: threshold should equal 0.991, 10 samples >= 0.991 (0.01 FPR)
    thresh_01, fpr_01 = select_threshold_from_scores(scores, target_fp_budget=0.01)
    assert math.isclose(thresh_01, 0.991, rel_tol=1e-5)
    assert math.isclose(fpr_01, 0.01, rel_tol=1e-5)
    assert sum(1 for s in scores if s >= thresh_01) / n <= 0.01

    # 5% FP budget: threshold should equal 0.951, 50 samples >= 0.951 (0.05 FPR)
    thresh_05, fpr_05 = select_threshold_from_scores(scores, target_fp_budget=0.05)
    assert math.isclose(thresh_05, 0.951, rel_tol=1e-5)
    assert math.isclose(fpr_05, 0.05, rel_tol=1e-5)
    assert sum(1 for s in scores if s >= thresh_05) / n <= 0.05

    # 10% FP budget: threshold should equal 0.901, 100 samples >= 0.901 (0.10 FPR)
    thresh_10, fpr_10 = select_threshold_from_scores(scores, target_fp_budget=0.10)
    assert math.isclose(thresh_10, 0.901, rel_tol=1e-5)
    assert math.isclose(fpr_10, 0.10, rel_tol=1e-5)
    assert sum(1 for s in scores if s >= thresh_10) / n <= 0.10


def test_gaussian_synthetic_distribution() -> None:
    """Gaussian synthetic scores respect empirical FP budget."""
    rng = random.Random(42)
    scores = [rng.gauss(0.0, 1.0) for _ in range(5000)]

    for budget in [0.005, 0.01, 0.02, 0.05, 0.10]:
        thresh, achieved_fpr = select_threshold_from_scores(scores, target_fp_budget=budget)
        assert achieved_fpr <= budget
        empirical_fpr = sum(1 for s in scores if s >= thresh) / len(scores)
        assert empirical_fpr == achieved_fpr
        assert empirical_fpr <= budget


def test_tied_scores_distribution_budget_enforcement() -> None:
    """Tied scores at discrete values respect budget constraints without violation."""
    # 800 zeros, 150 halves, 50 ones -> total 1000
    scores = [0.0] * 800 + [0.5] * 150 + [1.0] * 50

    # 5% budget matches exactly the 50 ones (50 / 1000 = 0.05)
    thresh_exact, fpr_exact = select_threshold_from_scores(scores, target_fp_budget=0.05)
    assert thresh_exact == 1.0
    assert fpr_exact == 0.05

    # 4% budget cannot accommodate all 50 ones, so threshold moves above 1.0 (yielding 0 FPs)
    thresh_under, fpr_under = select_threshold_from_scores(scores, target_fp_budget=0.04)
    assert thresh_under > 1.0
    assert fpr_under == 0.0
    assert fpr_under <= 0.04

    # 20% budget accommodates 50 ones + 150 halves (200 / 1000 = 0.20)
    thresh_twenty, fpr_twenty = select_threshold_from_scores(scores, target_fp_budget=0.20)
    assert thresh_twenty == 0.5
    assert fpr_twenty == 0.20


# =============================================================================
# Boundary & Edge Case Budgets
# =============================================================================


def test_zero_fp_budget() -> None:
    """A target budget of 0.0 yields threshold strictly above max score and 0.0 FPR."""
    scores = [0.1, 0.5, 0.9, 1.5]
    thresh, achieved_fpr = select_threshold_from_scores(scores, target_fp_budget=0.0)
    assert thresh > max(scores)
    assert achieved_fpr == 0.0
    assert sum(1 for s in scores if s >= thresh) == 0


def test_full_fp_budget() -> None:
    """A target budget of 1.0 yields min score as threshold and 1.0 FPR."""
    scores = [0.1, 0.5, 0.9, 1.5]
    thresh, achieved_fpr = select_threshold_from_scores(scores, target_fp_budget=1.0)
    assert thresh == min(scores)
    assert achieved_fpr == 1.0
    assert sum(1 for s in scores if s >= thresh) == len(scores)


def test_all_identical_scores() -> None:
    """When all validation scores are identical, budget constraints are met."""
    scores = [0.0] * 100
    # At 5% budget, cannot flag 100 samples, so threshold moves above 0.0
    thresh, achieved_fpr = select_threshold_from_scores(scores, target_fp_budget=0.05)
    assert thresh > 0.0
    assert achieved_fpr == 0.0

    # At 100% budget, all samples can be flagged
    thresh_full, achieved_full = select_threshold_from_scores(scores, target_fp_budget=1.0)
    assert thresh_full == 0.0
    assert achieved_full == 1.0


# =============================================================================
# End-to-End Calibration on Model without Lookahead Bias
# =============================================================================


class MockBaseline:
    """Conforming mock baseline returning deterministic score mappings."""

    def __init__(self, score_map: dict[str, float]) -> None:
        self.score_map = score_map

    def fit(self, clean_sequences: list[list[str]]) -> None:
        pass

    def score_sequence(self, sequence: list[str]) -> float:
        key = " -> ".join(sequence)
        return self.score_map.get(key, 0.0)

    def predict(self, sequence: list[str], threshold: float) -> bool:
        return self.score_sequence(sequence) >= threshold


def test_mock_baseline_conforms_to_protocol() -> None:
    """MockBaseline conforms to SequenceBaselineProtocol."""
    mock = MockBaseline({})
    assert isinstance(mock, SequenceBaselineProtocol)


def test_calibrate_threshold_end_to_end() -> None:
    """calibrate_threshold operates on clean validation set and achieves FP budget."""
    # 100 clean validation sessions with varying scores
    score_map: dict[str, float] = {}
    held_out_clean: list[list[str]] = []
    for i in range(100):
        seq = [f"GET /step/{i}", "POST /done"]
        key = " -> ".join(seq)
        score_val = (i + 1) / 100.0  # scores from 0.01 to 1.00
        score_map[key] = score_val
        held_out_clean.append(seq)

    mock = MockBaseline(score_map)
    thresh, achieved_fpr = calibrate_threshold(mock, held_out_clean, target_fp_budget=0.05)

    assert math.isclose(thresh, 0.96, rel_tol=1e-5)
    assert math.isclose(achieved_fpr, 0.05, rel_tol=1e-5)

    # Validate predictions match achieved FP count exactly
    flagged = sum(1 for seq in held_out_clean if mock.predict(seq, thresh))
    assert flagged == 5


def test_no_lookahead_bias_with_ngram() -> None:
    """Calibration computes threshold strictly from clean validation without attack leakage."""
    # Clean training data
    clean_train = [
        [
            "POST /cart/items",
            "POST /orders",
            "PUT /orders/{id}/address",
            "POST /orders/{id}/payment",
        ],
    ]
    baseline = NGramBaseline(n=2, alpha=0.01)
    baseline.fit(clean_train)

    # Clean validation sessions (evaluated without attacks)
    held_out_clean = [
        [
            "POST /cart/items",
            "POST /orders",
            "PUT /orders/{id}/address",
            "POST /orders/{id}/payment",
        ],
        ["POST /cart/items", "POST /orders"],
    ]

    thresh, achieved_fpr = calibrate_threshold(baseline, held_out_clean, target_fp_budget=0.10)
    assert achieved_fpr <= 0.10

    # Test evaluation: attack session is detected, clean sessions stay within budget
    attack_session = ["POST /orders", "POST /orders/{id}/fulfill"]  # skipped payment
    assert baseline.predict(attack_session, threshold=thresh)


# =============================================================================
# Input Validation Tests
# =============================================================================


def test_calibrate_threshold_invalid_baseline() -> None:
    """calibrate_threshold rejects objects not conforming to SequenceBaselineProtocol."""

    class Incomplete:
        def fit(self, clean_sequences: list[list[str]]) -> None:
            pass

    with pytest.raises(TypeError, match="must implement SequenceBaselineProtocol"):
        calibrate_threshold(Incomplete(), [["POST /orders"]], target_fp_budget=0.05)  # type: ignore[arg-type]


@pytest.mark.parametrize("invalid_budget", [-0.01, 1.01, -1.0, 5.0])
def test_calibrate_threshold_budget_out_of_bounds(invalid_budget: float) -> None:
    """Budgets outside [0.0, 1.0] raise BaselineValidationError."""
    mock = MockBaseline({})
    with pytest.raises(BaselineValidationError, match="must be in range"):
        calibrate_threshold(mock, [["POST /orders"]], target_fp_budget=invalid_budget)


@pytest.mark.parametrize("invalid_type", ["0.05", None, True, False, [0.05]])
def test_calibrate_threshold_budget_invalid_type(invalid_type: Any) -> None:
    """Non-numeric or boolean budgets raise TypeError."""
    mock = MockBaseline({})
    with pytest.raises(TypeError, match="target_fp_budget must be a float or int"):
        calibrate_threshold(mock, [["POST /orders"]], target_fp_budget=invalid_type)


@pytest.mark.parametrize("nan_budget", [float("nan"), float("inf"), float("-inf")])
def test_calibrate_threshold_budget_nan_inf(nan_budget: float) -> None:
    """NaN and infinite budgets raise BaselineValidationError."""
    mock = MockBaseline({})
    with pytest.raises(BaselineValidationError, match="cannot be NaN or infinite"):
        calibrate_threshold(mock, [["POST /orders"]], target_fp_budget=nan_budget)


def test_calibrate_threshold_empty_held_out() -> None:
    """Empty held_out_clean raises BaselineValidationError."""
    mock = MockBaseline({})
    with pytest.raises(BaselineValidationError, match="held_out_clean cannot be empty"):
        calibrate_threshold(mock, [], target_fp_budget=0.05)
