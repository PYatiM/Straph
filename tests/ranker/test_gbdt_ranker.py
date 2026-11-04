"""Unit tests for GBDTRanker gradient-boosted priority ranking model."""

from __future__ import annotations

from pathlib import Path

import pytest

from services.ranker.gbdt_ranker import GBDTRanker


@pytest.fixture
def synthetic_training_data() -> tuple[list[dict[str, float]], list[int]]:
    """Create separable synthetic dataset representing high vs low priority alerts."""
    feature_matrix: list[dict[str, float]] = []
    labels: list[int] = []

    # High-priority alerts: R0/R1, accepted, high role mismatch or missing steps
    for _ in range(25):
        feature_matrix.append(
            {
                "rule_severity_code": 4.0,
                "is_accepted": 1.0,
                "has_role_mismatch": 1.0,
                "missing_steps_count": 2.0,
                "history_length": 5.0,
                "transition_rarity": 0.8,
            }
        )
        labels.append(1)

    # Low-priority alerts: R4/R3, attempted, no mismatch, zero missing
    for _ in range(25):
        feature_matrix.append(
            {
                "rule_severity_code": 0.5,
                "is_accepted": 0.0,
                "has_role_mismatch": 0.0,
                "missing_steps_count": 0.0,
                "history_length": 1.0,
                "transition_rarity": 0.01,
            }
        )
        labels.append(0)

    return feature_matrix, labels


def test_train_and_predict_rank_bounds(
    synthetic_training_data: tuple[list[dict[str, float]], list[int]],
) -> None:
    """Trained GBDTRanker must produce probability scores strictly in [0.0, 1.0]."""
    X, y = synthetic_training_data
    ranker = GBDTRanker()
    assert not ranker.is_fitted

    ranker.train(X, y)
    assert ranker.is_fitted

    high_feat = {
        "rule_severity_code": 4.0,
        "is_accepted": 1.0,
        "has_role_mismatch": 1.0,
        "missing_steps_count": 2.0,
        "history_length": 5.0,
        "transition_rarity": 0.9,
    }
    low_feat = {
        "rule_severity_code": 0.5,
        "is_accepted": 0.0,
        "has_role_mismatch": 0.0,
        "missing_steps_count": 0.0,
        "history_length": 1.0,
        "transition_rarity": 0.0,
    }

    score_high = ranker.predict_rank(high_feat)
    score_low = ranker.predict_rank(low_feat)

    assert 0.0 <= score_high <= 1.0
    assert 0.0 <= score_low <= 1.0
    assert score_high > score_low
    assert score_high >= 0.8
    assert score_low <= 0.2


def test_unfitted_ranker_raises() -> None:
    """Predicting or saving before fitting must raise RuntimeError."""
    ranker = GBDTRanker()
    with pytest.raises(RuntimeError, match="not fitted yet"):
        ranker.predict_rank({"rule_severity_code": 1.0})

    with pytest.raises(RuntimeError, match="Cannot save an unfitted"):
        ranker.save_model("dummy.pkl")


def test_invalid_training_arguments() -> None:
    """Empty or mismatched inputs must raise ValueError."""
    ranker = GBDTRanker()
    with pytest.raises(ValueError, match="non-empty"):
        ranker.train([], [])

    with pytest.raises(ValueError, match="Length mismatch"):
        ranker.train([{"rule_severity_code": 1.0}], [1, 0])


def test_save_and_load_model(
    synthetic_training_data: tuple[list[dict[str, float]], list[int]],
    tmp_path: Path,
) -> None:
    """Serialized GBDTRanker must produce identical predictions after deserialization."""
    X, y = synthetic_training_data
    ranker = GBDTRanker()
    ranker.train(X, y)

    model_path = tmp_path / "gbdt_model.pkl"
    ranker.save_model(model_path)
    assert model_path.exists()

    loaded_ranker = GBDTRanker.from_file(model_path)
    assert loaded_ranker.is_fitted

    test_feat = {
        "rule_severity_code": 3.0,
        "is_accepted": 1.0,
        "has_role_mismatch": 0.0,
        "missing_steps_count": 1.0,
        "history_length": 3.0,
        "transition_rarity": 0.4,
    }
    score_orig = ranker.predict_rank(test_feat)
    score_loaded = loaded_ranker.predict_rank(test_feat)

    assert score_orig == pytest.approx(score_loaded, abs=1e-6)
