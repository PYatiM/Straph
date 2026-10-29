"""Unit tests for BaselineManager.

Verifies:
- train_all() fits all three baselines (NGram, Markov, Trace Invariant).
- evaluate_sequence() returns consolidated anomaly decisions from all three baselines.
- Canonical and display key access (e.g. 'ngram' and 'N-gram').
- Clean workflows evaluate to False across baselines; anomalous workflows evaluate to True.
- Roundtrip disk serialization and restoration across both JSON and Pickle formats.
- Error handling for unfitted models and missing directories.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from services.evaluation.baselines.interfaces import (
    BaselineNotFittedError,
    BaselineValidationError,
)
from services.evaluation.baselines.manager import (
    BaselineEvaluationResult,
    BaselineManager,
)

# Standard test workflows
CLEAN_CHECKOUT_FLOW_1: list[str] = [
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

CLEAN_CHECKOUT_FLOW_2: list[str] = [
    "POST /cart/items",
    "POST /cart/items",
    "POST /orders",
    "PUT /orders/{id}/address",
    "POST /orders/{id}/payment",
    "POST /payments/webhook",
    "POST /orders/{id}/fulfill",
    "POST /orders/{id}/cancel",
]

CLEAN_TRACES = [CLEAN_CHECKOUT_FLOW_1, CLEAN_CHECKOUT_FLOW_2]

# TC2 checkout skip: skips payment and webhook
CHECKOUT_SKIP_PAYMENT_FLOW: list[str] = [
    "POST /cart/items",
    "POST /orders",
    "PUT /orders/{id}/address",
    "POST /orders/{id}/fulfill",  # illegal skip
    "POST /orders/{id}/ship",
]

# TC4 double-refund: replay single-use refund
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
    "POST /orders/{id}/refund",  # duplicate single-use op
]


# =============================================================================
# Training & Fitting
# =============================================================================


def test_train_all_fits_all_three_baselines() -> None:
    """train_all() fits NGram, Markov, and Trace Invariant models simultaneously."""
    manager = BaselineManager()
    assert manager.is_fitted is False

    manager.train_all(CLEAN_TRACES)

    assert manager.is_fitted is True
    assert manager.ngram.is_fitted is True
    assert manager.markov.is_fitted is True
    assert manager.trace_invariant.is_fitted is True


def test_train_all_with_invalid_traces_raises() -> None:
    """train_all() validates input traces."""
    manager = BaselineManager()
    with pytest.raises(BaselineValidationError):
        manager.train_all([])


# =============================================================================
# Consolidated Evaluation
# =============================================================================


def test_evaluate_sequence_clean_flow() -> None:
    """Clean training sequences evaluate to False (normal) across all three baselines."""
    manager = BaselineManager()
    manager.train_all(CLEAN_TRACES)

    result = manager.evaluate_sequence(CLEAN_CHECKOUT_FLOW_1)

    assert isinstance(result, BaselineEvaluationResult)
    assert isinstance(result, dict)

    # All baselines agree clean sequence is normal
    assert result["ngram"] is False
    assert result["markov"] is False
    assert result["trace_invariant"] is False
    assert result.is_anomaly is False

    # Aliases also resolve properly
    assert result["N-gram"] is False
    assert result["Markov"] is False
    assert result["Trace Invariant"] is False


def test_evaluate_sequence_checkout_skip_anomalous_flow() -> None:
    """Checkout skip (skipping payment) is flagged as anomalous by all three baselines."""
    manager = BaselineManager()
    manager.train_all(CLEAN_TRACES)

    result = manager.evaluate_sequence(CHECKOUT_SKIP_PAYMENT_FLOW)

    assert result["ngram"] is True
    assert result["markov"] is True
    assert result["trace_invariant"] is True
    assert result.is_anomaly is True


def test_evaluate_sequence_double_refund_flow() -> None:
    """Double-refund replay triggers trace invariant violation."""
    manager = BaselineManager()
    manager.train_all(CLEAN_TRACES)

    result = manager.evaluate_sequence(DOUBLE_REFUND_FLOW)

    assert result["trace_invariant"] is True
    assert result.is_anomaly is True


def test_score_all_returns_continuous_anomaly_scores() -> None:
    """score_all() returns non-negative continuous anomaly scores for all baselines."""
    manager = BaselineManager()
    manager.train_all(CLEAN_TRACES)

    scores = manager.score_all(CHECKOUT_SKIP_PAYMENT_FLOW)
    assert set(scores.keys()) == {"ngram", "markov", "trace_invariant"}
    assert all(isinstance(v, float) and v >= 0.0 for v in scores.values())
    assert scores["trace_invariant"] > 0.0


# =============================================================================
# Roundtrip Disk Serialization (JSON and Pickle)
# =============================================================================


def test_roundtrip_disk_serialization_json(tmp_path: Path) -> None:
    """Models can be saved to disk as JSON and restored with matching predictions."""
    save_dir = tmp_path / "models_json"
    manager = BaselineManager()
    manager.train_all(CLEAN_TRACES)

    clean_pred_before = manager.evaluate_sequence(CLEAN_CHECKOUT_FLOW_1)
    anom_pred_before = manager.evaluate_sequence(CHECKOUT_SKIP_PAYMENT_FLOW)

    # Save to disk
    manager.save_all(save_dir, format="json")

    # Assert expected files exist
    assert (save_dir / "ngram.json").is_file()
    assert (save_dir / "markov.json").is_file()
    assert (save_dir / "trace_invariant.json").is_file()
    assert (save_dir / "metadata.json").is_file()

    # 1. Restore into existing manager instance via load_all()
    restored_instance = BaselineManager()
    ret = restored_instance.load_all(save_dir)
    assert ret is restored_instance
    assert restored_instance.is_fitted is True

    clean_pred_after = restored_instance.evaluate_sequence(CLEAN_CHECKOUT_FLOW_1)
    anom_pred_after = restored_instance.evaluate_sequence(CHECKOUT_SKIP_PAYMENT_FLOW)

    assert clean_pred_after == clean_pred_before
    assert anom_pred_after == anom_pred_before

    # 2. Restore via classmethod from_directory()
    restored_cls = BaselineManager.from_directory(save_dir)
    assert restored_cls.is_fitted is True
    assert restored_cls.evaluate_sequence(CHECKOUT_SKIP_PAYMENT_FLOW) == anom_pred_before


def test_roundtrip_disk_serialization_pickle(tmp_path: Path) -> None:
    """Models can be saved to disk as binary pickle and restored with matching predictions."""
    save_dir = tmp_path / "models_pkl"
    manager = BaselineManager()
    manager.train_all(CLEAN_TRACES)

    anom_pred_before = manager.evaluate_sequence(CHECKOUT_SKIP_PAYMENT_FLOW)

    manager.save_all(save_dir, format="pkl")

    assert (save_dir / "ngram.pkl").is_file()
    assert (save_dir / "markov.pkl").is_file()
    assert (save_dir / "trace_invariant.pkl").is_file()

    restored = BaselineManager.from_directory(save_dir)
    assert restored.is_fitted is True
    assert restored.evaluate_sequence(CHECKOUT_SKIP_PAYMENT_FLOW) == anom_pred_before


# =============================================================================
# Calibration Integration
# =============================================================================


def test_calibrate_all_updates_thresholds() -> None:
    """calibrate_all() updates thresholds on held-out clean validation traces."""
    manager = BaselineManager()
    manager.train_all([CLEAN_CHECKOUT_FLOW_1])

    calib_results = manager.calibrate_all([CLEAN_CHECKOUT_FLOW_2], fp_budget=0.05)
    assert "ngram" in calib_results
    assert "markov" in calib_results
    assert "trace_invariant" in calib_results
    assert "ngram" in manager.thresholds
    assert "markov" in manager.thresholds


# =============================================================================
# Error Handling
# =============================================================================


def test_unfitted_manager_raises_on_evaluate() -> None:
    """Calling evaluate_sequence() on unfitted manager raises BaselineNotFittedError."""
    manager = BaselineManager()
    with pytest.raises(BaselineNotFittedError):
        manager.evaluate_sequence(CLEAN_CHECKOUT_FLOW_1)


def test_unfitted_manager_raises_on_score() -> None:
    """Calling score_all() on unfitted manager raises BaselineNotFittedError."""
    manager = BaselineManager()
    with pytest.raises(BaselineNotFittedError):
        manager.score_all(CLEAN_CHECKOUT_FLOW_1)


def test_load_all_missing_directory_raises(tmp_path: Path) -> None:
    """Loading from a non-existent directory raises FileNotFoundError."""
    manager = BaselineManager()
    with pytest.raises(FileNotFoundError):
        manager.load_all(tmp_path / "nonexistent_directory")


def test_load_all_missing_model_files_raises(tmp_path: Path) -> None:
    """Loading from an empty directory missing baseline files raises FileNotFoundError."""
    empty_dir = tmp_path / "empty_dir"
    empty_dir.mkdir()
    manager = BaselineManager()
    with pytest.raises(FileNotFoundError):
        manager.load_all(empty_dir)

