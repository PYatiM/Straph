"""Unified baseline manager for sequence-based anomaly detection models.

Coordinates training, serialization, deserialization, and evaluation across:
1. NGramBaseline: Frequency-based n-gram sequence anomaly model.
2. FirstOrderMarkovBaseline: Conditional transition matrix probability model.
3. TraceInvariantBaseline: Allowed transition pairs and single-use invariant model.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Self

from services.evaluation.baselines.calibration import calibrate_threshold
from services.evaluation.baselines.interfaces import (
    BaselineNotFittedError,
    BaseSequenceBaseline,
)
from services.evaluation.baselines.markov import FirstOrderMarkovBaseline
from services.evaluation.baselines.ngram import NGramBaseline
from services.evaluation.baselines.trace_invariant import TraceInvariantBaseline

__all__ = [
    "BaselineEvaluationResult",
    "BaselineManager",
]


class BaselineEvaluationResult(dict[str, bool]):
    """Consolidated evaluation decisions dictionary supporting canonical and display keys."""

    _KEY_ALIASES: Mapping[str, str] = {
        "ngram": "ngram",
        "n-gram": "ngram",
        "markov": "markov",
        "trace_invariant": "trace_invariant",
        "trace invariant": "trace_invariant",
        "trace-invariant": "trace_invariant",
    }

    def __getitem__(self, key: str) -> bool:
        normalized = self._KEY_ALIASES.get(key.lower(), key)
        return super().__getitem__(normalized)

    def get(self, key: str, default: Any = None) -> Any:
        normalized = self._KEY_ALIASES.get(key.lower(), key)
        return super().get(normalized, default)

    @property
    def is_anomaly(self) -> bool:
        """Return True if any baseline flagged an anomaly."""
        return any(self.values())


class BaselineManager:
    """Unified coordinator for sequence-based IDS baselines.

    Manages training, multi-format disk serialization, and consolidated sequence
    evaluation across NGramBaseline, FirstOrderMarkovBaseline, and TraceInvariantBaseline.
    """

    def __init__(
        self,
        ngram: NGramBaseline | None = None,
        markov: FirstOrderMarkovBaseline | None = None,
        trace_invariant: TraceInvariantBaseline | None = None,
        *,
        thresholds: dict[str, float] | None = None,
    ) -> None:
        """Initialize BaselineManager.

        Args:
            ngram: Optional pre-configured NGramBaseline instance.
            markov: Optional pre-configured FirstOrderMarkovBaseline instance.
            trace_invariant: Optional pre-configured TraceInvariantBaseline instance.
            thresholds: Optional dictionary of decision thresholds keyed by baseline name.
        """
        self.ngram: NGramBaseline = ngram if ngram is not None else NGramBaseline()
        self.markov: FirstOrderMarkovBaseline = (
            markov if markov is not None else FirstOrderMarkovBaseline()
        )
        self.trace_invariant: TraceInvariantBaseline = (
            trace_invariant if trace_invariant is not None else TraceInvariantBaseline()
        )
        self.thresholds: dict[str, float] = dict(thresholds) if thresholds is not None else {}
        self._default_thresholds: dict[str, float] = {}

    @property
    def is_fitted(self) -> bool:
        """Return True if all three underlying baselines are fitted."""
        return self.ngram.is_fitted and self.markov.is_fitted and self.trace_invariant.is_fitted

    # -------------------------------------------------------------------------
    # Training & Calibration
    # -------------------------------------------------------------------------

    def train_all(
        self,
        clean_traces: list[list[str]],
        held_out_validation: list[list[str]] | None = None,
        fp_budget: float = 0.01,
    ) -> None:
        """Train all three baselines on clean trace sessions.

        Args:
            clean_traces: List of clean API operation sequences.
            held_out_validation: Optional held-out clean traces for empirical threshold calibration.
            fp_budget: Target false-positive budget in [0.0, 1.0] when calibrating.
        """
        BaseSequenceBaseline.validate_clean_sequences(clean_traces)

        # Fit all three baselines
        self.ngram.fit(clean_traces)
        self.markov.fit(clean_traces)
        self.trace_invariant.fit(clean_traces)

        # Establish empirical score thresholds
        if held_out_validation:
            self.calibrate_all(held_out_validation, fp_budget=fp_budget)
        else:
            # Set default thresholds just above the max clean training score
            max_ngram = max(self.ngram.score_sequence(seq) for seq in clean_traces)
            max_markov = max(self.markov.score_sequence(seq) for seq in clean_traces)
            self._default_thresholds = {
                "ngram": max_ngram + 1e-4,
                "markov": max_markov + 1e-4,
            }
            for k, v in self._default_thresholds.items():
                if k not in self.thresholds:
                    self.thresholds[k] = v

    def calibrate_all(
        self,
        held_out_clean: list[list[str]],
        fp_budget: float = 0.01,
    ) -> dict[str, tuple[float, float]]:
        """Calibrate decision thresholds on held-out clean data subject to FP budget.

        Args:
            held_out_clean: List of held-out clean validation sessions.
            fp_budget: False-positive budget in [0.0, 1.0].

        Returns:
            Dictionary mapping baseline name to (threshold, achieved_fp_rate).
        """
        if not self.is_fitted:
            raise BaselineNotFittedError("All baselines must be fitted before calibration.")

        tau_ngram, fpr_ngram = calibrate_threshold(self.ngram, held_out_clean, fp_budget)
        tau_markov, fpr_markov = calibrate_threshold(self.markov, held_out_clean, fp_budget)

        self.thresholds["ngram"] = tau_ngram
        self.thresholds["markov"] = tau_markov
        self.thresholds.pop("trace_invariant", None)

        return {
            "ngram": (tau_ngram, fpr_ngram),
            "markov": (tau_markov, fpr_markov),
            "trace_invariant": (0.0, 0.0),
        }

    # -------------------------------------------------------------------------
    # Evaluation
    # -------------------------------------------------------------------------

    def evaluate_sequence(
        self,
        sequence: list[str],
        thresholds: dict[str, float] | None = None,
    ) -> BaselineEvaluationResult:
        """Return anomaly decisions from all three baselines for a sequence.

        Args:
            sequence: Sequence of observed API operations.
            thresholds: Optional per-call threshold overrides.

        Returns:
            BaselineEvaluationResult with decisions for 'ngram', 'markov', and 'trace_invariant'.
        """
        if not self.is_fitted:
            raise BaselineNotFittedError(
                "Cannot evaluate sequence before all baselines are fitted or loaded."
            )

        active_thresholds = dict(self.thresholds)
        if thresholds:
            active_thresholds.update(thresholds)

        tau_ngram = active_thresholds.get("ngram", self._default_thresholds.get("ngram", 1e-4))
        tau_markov = active_thresholds.get("markov", self._default_thresholds.get("markov", 1e-4))
        tau_ti = active_thresholds.get("trace_invariant")

        # 1. NGram prediction
        ngram_pred = self.ngram.predict(sequence, threshold=tau_ngram)

        # 2. Markov prediction
        markov_pred = self.markov.predict(sequence, threshold=tau_markov)

        # 3. Trace invariant prediction
        ti_pred = self.trace_invariant.predict(sequence, threshold=tau_ti)

        return BaselineEvaluationResult(
            {
                "ngram": ngram_pred,
                "markov": markov_pred,
                "trace_invariant": ti_pred,
            }
        )

    def score_all(self, sequence: list[str]) -> dict[str, float]:
        """Compute raw continuous anomaly scores across all three baselines.

        Args:
            sequence: Sequence of API operations.

        Returns:
            Dictionary of anomaly scores keyed by baseline name.
        """
        if not self.is_fitted:
            raise BaselineNotFittedError("All baselines must be fitted before scoring.")

        return {
            "ngram": self.ngram.score_sequence(sequence),
            "markov": self.markov.score_sequence(sequence),
            "trace_invariant": self.trace_invariant.score_sequence(sequence),
        }

    # -------------------------------------------------------------------------
    # Disk Serialization & Deserialization
    # -------------------------------------------------------------------------

    def save_all(self, directory: str | Path, format: str = "json") -> None:
        """Save all three baseline models and manager metadata to a directory on disk.

        Args:
            directory: Target directory path.
            format: Serialization format ('json' or 'pkl'). Defaults to 'json'.
        """
        dest_dir = Path(directory)
        dest_dir.mkdir(parents=True, exist_ok=True)

        ext = f".{format.lstrip('.')}"
        self.ngram.save(dest_dir / f"ngram{ext}")
        self.markov.save(dest_dir / f"markov{ext}")
        self.trace_invariant.save(dest_dir / f"trace_invariant{ext}")

        # Save metadata and thresholds
        metadata = {
            "format": format,
            "thresholds": self.thresholds,
            "default_thresholds": self._default_thresholds,
            "is_fitted": self.is_fitted,
        }
        with open(dest_dir / "metadata.json", "w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=2)

    def _load_all_from_path(self, directory: str | Path) -> None:
        """Internal helper to load all models from directory into current instance."""
        source_dir = Path(directory)
        if not source_dir.is_dir():
            raise FileNotFoundError(f"Baseline directory not found: {source_dir}")

        # Detect model serialization file formats
        ngram_path = source_dir / "ngram.json"
        if not ngram_path.exists():
            ngram_path = source_dir / "ngram.pkl"

        markov_path = source_dir / "markov.json"
        if not markov_path.exists():
            markov_path = source_dir / "markov.pkl"

        ti_path = source_dir / "trace_invariant.json"
        if not ti_path.exists():
            ti_path = source_dir / "trace_invariant.pkl"

        if not ngram_path.exists():
            raise FileNotFoundError(f"Missing NGram model in {source_dir}")
        if not markov_path.exists():
            raise FileNotFoundError(f"Missing Markov model in {source_dir}")
        if not ti_path.exists():
            raise FileNotFoundError(f"Missing Trace Invariant model in {source_dir}")

        self.ngram = NGramBaseline.load(ngram_path)
        self.markov = FirstOrderMarkovBaseline.load(markov_path)
        self.trace_invariant = TraceInvariantBaseline.load(ti_path)

        metadata_path = source_dir / "metadata.json"
        if metadata_path.is_file():
            try:
                with open(metadata_path, "r", encoding="utf-8") as f:
                    meta = json.load(f)
                self.thresholds = meta.get("thresholds", {})
                self._default_thresholds = meta.get("default_thresholds", {})
            except (json.JSONDecodeError, OSError):
                pass

    def load_all(self, directory: str | Path) -> Self:
        """Load all three baseline models and metadata from disk.

        Can be called on an instance or used to populate models from disk.

        Args:
            directory: Source directory containing saved baseline models.

        Returns:
            The loaded BaselineManager instance.
        """
        self._load_all_from_path(directory)
        return self

    @classmethod
    def from_directory(cls, directory: str | Path) -> Self:
        """Construct and load a new BaselineManager from a saved directory.

        Args:
            directory: Directory containing saved baseline models.

        Returns:
            Fresh BaselineManager instance with restored models and thresholds.
        """
        manager = cls()
        manager._load_all_from_path(directory)
        return manager
