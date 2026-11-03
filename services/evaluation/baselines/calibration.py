"""Threshold calibration utilities for sequence-based IDS baselines.

Determines empirical anomaly score thresholds on held-out clean validation sessions
subject to a specified false-positive budget constraint.
"""

from __future__ import annotations

import bisect
import math
from collections.abc import Sequence

from services.evaluation.baselines.interfaces import (
    BaselineValidationError,
    BaseSequenceBaseline,
    SequenceBaselineProtocol,
)


def select_threshold_from_scores(
    scores: Sequence[float],
    target_fp_budget: float,
) -> tuple[float, float]:
    """Select the minimum empirical decision threshold satisfying a false-positive budget.

    Given a collection of clean validation anomaly scores, finds the lowest threshold tau
    such that the achieved false-positive rate:
        FPR(tau) = sum(score >= tau for score in scores) / len(scores)
    does not exceed target_fp_budget.

    Args:
        scores: Sequence of validation anomaly scores.
        target_fp_budget: Maximum allowed false-positive rate in [0.0, 1.0].

    Returns:
        tuple[float, float]: (threshold, achieved_fp_rate)

    Raises:
        BaselineValidationError: If scores is empty or target_fp_budget not in [0.0, 1.0].
        TypeError: If argument types are invalid.
    """
    if isinstance(target_fp_budget, bool) or not isinstance(target_fp_budget, (int, float)):
        raise TypeError(
            f"target_fp_budget must be a float or int, got {type(target_fp_budget).__name__}"
        )
    if math.isnan(target_fp_budget) or math.isinf(target_fp_budget):
        raise BaselineValidationError("target_fp_budget cannot be NaN or infinite")
    if not (0.0 <= target_fp_budget <= 1.0):
        raise BaselineValidationError(
            f"target_fp_budget must be in range [0.0, 1.0], got {target_fp_budget}"
        )

    if not isinstance(scores, (list, tuple)):
        raise TypeError(f"scores must be a sequence of floats, got {type(scores).__name__}")
    if len(scores) == 0:
        raise BaselineValidationError("scores cannot be empty")

    n = len(scores)
    sorted_scores = sorted(float(s) for s in scores)
    max_allowed_fp = math.floor(n * target_fp_budget)

    # Special case: zero false-positive tolerance (0.0 budget)
    if max_allowed_fp == 0:
        threshold = sorted_scores[-1] + 1e-6
        return (float(threshold), 0.0)

    # Special case: 100% budget allows all samples to be flagged
    if max_allowed_fp >= n:
        threshold = sorted_scores[0]
        return (float(threshold), 1.0)

    # Search distinct threshold candidates in ascending order to find the lowest
    # threshold whose number of false positives (score >= tau) <= max_allowed_fp
    unique_scores = sorted(set(sorted_scores))
    for val in unique_scores:
        idx = bisect.bisect_left(sorted_scores, val)
        fp_count = n - idx
        if fp_count <= max_allowed_fp:
            achieved_fp_rate = fp_count / n
            return (float(val), float(achieved_fp_rate))

    # If ties at the top prevent any score from achieving <= max_allowed_fp,
    # set threshold just above maximum score yielding 0 false positives
    threshold = sorted_scores[-1] + 1e-6
    return (float(threshold), 0.0)


def calibrate_threshold(
    baseline: SequenceBaselineProtocol,
    held_out_clean: list[list[str]],
    target_fp_budget: float,
) -> tuple[float, float]:
    """Calibrate decision threshold on held-out clean sessions subject to an FP budget.

    Computes anomaly scores for all clean validation sessions, sorts them, and determines
    the empirical threshold tau such that the achieved false-positive rate does not exceed
    target_fp_budget (e.g. 0.01 for 1%).

    Calibration is computed strictly on held-out clean sequences without lookahead bias.

    Args:
        baseline: Fitted baseline model conforming to SequenceBaselineProtocol.
        held_out_clean: Clean validation sequences.
        target_fp_budget: Upper bound on acceptable false-positive rate in [0.0, 1.0].

    Returns:
        tuple[float, float]: (threshold, achieved_fp_rate)

    Raises:
        BaselineValidationError: If held_out_clean is empty or budget is out of bounds.
        TypeError: If input types are invalid.
    """
    if not isinstance(baseline, SequenceBaselineProtocol):
        raise TypeError(
            f"baseline must implement SequenceBaselineProtocol, got {type(baseline).__name__}"
        )

    if not isinstance(held_out_clean, list):
        raise TypeError(
            f"held_out_clean must be a list of sequences, got {type(held_out_clean).__name__}"
        )
    if len(held_out_clean) == 0:
        raise BaselineValidationError("held_out_clean cannot be empty")

    BaseSequenceBaseline.validate_clean_sequences(held_out_clean)

    scores = [baseline.score_sequence(seq) for seq in held_out_clean]
    return select_threshold_from_scores(scores, target_fp_budget)
