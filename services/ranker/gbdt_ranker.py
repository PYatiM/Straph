"""Gradient-Boosted Decision Tree alert priority ranker based on scikit-learn."""

from __future__ import annotations

import pickle
from pathlib import Path
from typing import Any

from sklearn.ensemble import GradientBoostingClassifier

__all__ = [
    "DEFAULT_FEATURE_NAMES",
    "GBDTRanker",
]

DEFAULT_FEATURE_NAMES: list[str] = [
    "rule_severity_code",
    "is_accepted",
    "has_role_mismatch",
    "missing_steps_count",
    "history_length",
    "transition_rarity",
]


class GBDTRanker:
    """Gradient-Boosted Decision Tree priority ranker for security alerts."""

    def __init__(
        self,
        classifier: GradientBoostingClassifier | None = None,
        feature_names: list[str] | None = None,
        random_state: int = 42,
    ) -> None:
        self.feature_names: list[str] = (
            list(feature_names) if feature_names is not None else list(DEFAULT_FEATURE_NAMES)
        )
        self.classifier: GradientBoostingClassifier | None = classifier
        self.random_state: int = random_state
        self._is_fitted: bool = classifier is not None and hasattr(classifier, "classes_")

    @property
    def is_fitted(self) -> bool:
        """Whether the model is fitted and ready for inference."""
        return self._is_fitted

    def _features_to_vector(self, features: dict[str, float]) -> list[float]:
        """Convert a feature dict into an ordered float vector."""
        return [float(features.get(name, 0.0)) for name in self.feature_names]

    def train(
        self,
        feature_matrix: list[dict[str, float]],
        labels: list[int],
        **kwargs: Any,
    ) -> None:
        """Fit binary classifier predicting high-priority (1) vs low-priority (0) alerts.

        Args:
            feature_matrix: List of feature dictionaries per training alert.
            labels: Binary class labels (1 for high-priority, 0 for low-priority).
            **kwargs: Extra parameters passed to GradientBoostingClassifier.
        """
        if not feature_matrix or not labels:
            raise ValueError("feature_matrix and labels must be non-empty lists.")
        if len(feature_matrix) != len(labels):
            raise ValueError(
                f"Length mismatch: {len(feature_matrix)} feature vectors vs {len(labels)} labels."
            )

        X = [self._features_to_vector(f) for f in feature_matrix]
        y = [int(lbl) for lbl in labels]

        params: dict[str, Any] = {
            "n_estimators": 50,
            "learning_rate": 0.1,
            "max_depth": 3,
            "random_state": self.random_state,
        }
        params.update(kwargs)

        clf = GradientBoostingClassifier(**params)
        clf.fit(X, y)

        self.classifier = clf
        self._is_fitted = True

    def predict_rank(self, features: dict[str, float]) -> float:
        """Predict the probability that an alert is high-priority in [0.0, 1.0].

        Args:
            features: Dictionary containing numeric features for the alert.

        Returns:
            Probability of the positive (high priority) class bounded in [0.0, 1.0].
        """
        if not self._is_fitted or self.classifier is None:
            raise RuntimeError(
                "GBDTRanker is not fitted yet. Call 'train' or 'load_model' before predicting."
            )

        x = self._features_to_vector(features)
        proba = self.classifier.predict_proba([x])[0]

        classes = list(self.classifier.classes_)
        if 1 in classes:
            idx = classes.index(1)
            score = float(proba[idx])
        elif len(classes) == 1:
            score = 1.0 if classes[0] == 1 else 0.0
        else:
            score = float(proba[-1])

        return max(0.0, min(1.0, score))

    def save_model(self, path: str | Path) -> None:
        """Serialize fitted model and feature schema to disk."""
        if not self._is_fitted or self.classifier is None:
            raise RuntimeError("Cannot save an unfitted GBDTRanker.")

        dest = Path(path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "classifier": self.classifier,
            "feature_names": self.feature_names,
            "random_state": self.random_state,
        }
        with open(dest, "wb") as f:
            pickle.dump(payload, f)

    def load_model(self, path: str | Path) -> None:
        """Load serialized model and feature schema from disk."""
        src = Path(path)
        if not src.exists():
            raise FileNotFoundError(f"Model file not found at {src}")

        with open(src, "rb") as f:
            payload = pickle.load(f)

        self.classifier = payload["classifier"]
        self.feature_names = payload.get("feature_names", list(DEFAULT_FEATURE_NAMES))
        self.random_state = payload.get("random_state", 42)
        self._is_fitted = True

    @classmethod
    def from_file(cls, path: str | Path) -> GBDTRanker:
        """Factory creating and loading a GBDTRanker from disk."""
        ranker = cls()
        ranker.load_model(path)
        return ranker
