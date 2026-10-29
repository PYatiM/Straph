"""Interfaces and base classes for sequence-based anomaly detection baselines.

Provides:
- SequenceBaselineProtocol: Runtime-checkable protocol enforcing fit, score_sequence, and predict.
- BaseSequenceBaseline: Abstract base class implementing input normalization, validation,
  fitted-state checking, prediction logic, and model serialization (save/load).
"""

from __future__ import annotations

import abc
import copy
import inspect
import json
import math
import pickle
import re
from pathlib import Path
from typing import Any, Protocol, Self, runtime_checkable


class BaselineError(Exception):
    """Base exception for all sequence baseline errors."""


class BaselineNotFittedError(BaselineError, RuntimeError):
    """Raised when an operation requiring a fitted baseline is called before fitting."""


class BaselineValidationError(BaselineError, ValueError):
    """Raised when sequence, parameter, or threshold validation fails."""


@runtime_checkable
class SequenceBaselineProtocol(Protocol):
    """Protocol for sequence-based anomaly detection baselines."""

    def fit(self, clean_sequences: list[list[str]]) -> None:
        """Fit the baseline model on clean/normal sequences.

        Args:
            clean_sequences: List of clean API operation sequences.
        """
        ...

    def score_sequence(self, sequence: list[str]) -> float:
        """Score an observed sequence of API operations.

        Args:
            sequence: Sequence of API operations.

        Returns:
            Anomaly score as a float. Higher values indicate higher anomaly likelihood.
        """
        ...

    def predict(self, sequence: list[str], threshold: float) -> bool:
        """Predict whether a sequence is anomalous based on threshold.

        Args:
            sequence: Sequence of API operations.
            threshold: Decision threshold for anomaly classification.

        Returns:
            True if sequence is classified as anomalous, False if normal.
        """
        ...


class BaseSequenceBaseline(abc.ABC):
    """Abstract base class for sequence anomaly detection baselines.

    Implements:
    - Input normalization (HTTP method + path template -> canonical operation string).
    - Common sequence, clean_sequences, and threshold validation.
    - Fitted-state management and verification.
    - Standard threshold-based prediction logic.
    - Model serialization and deserialization supporting JSON and binary formats.
    """

    def __init__(self, **params: Any) -> None:
        """Initialize the baseline instance with optional hyperparameters."""
        self._is_fitted: bool = False
        self._params: dict[str, Any] = dict(params)
        self._state: dict[str, Any] = {}

    @property
    def is_fitted(self) -> bool:
        """Return True if the model has been fitted, False otherwise."""
        return self._is_fitted

    @property
    def params(self) -> dict[str, Any]:
        """Return a copy of the hyperparameters."""
        return copy.deepcopy(self._params)

    # -------------------------------------------------------------------------
    # Input Normalization (method + path template)
    # -------------------------------------------------------------------------

    @staticmethod
    def normalize_operation(method: str, path_template: str) -> str:
        """Normalize an HTTP method and path template into a canonical operation string.

        Args:
            method: HTTP method (e.g. 'post', 'GET').
            path_template: Path route template (e.g. ' /orders/{id}/ ', 'cart/items').

        Returns:
            Canonical operation string in 'METHOD /path' format, e.g. 'POST /orders/{id}'.

        Raises:
            BaselineValidationError: If method or path_template is not a non-empty string.
        """
        if not isinstance(method, str) or not method.strip():
            raise BaselineValidationError("HTTP method must be a non-empty string.")
        if not isinstance(path_template, str) or not path_template.strip():
            raise BaselineValidationError("Path template must be a non-empty string.")

        clean_method = method.strip().upper()
        clean_path = path_template.strip()

        # Collapse duplicate slashes into a single slash
        clean_path = re.sub(r"/+", "/", clean_path)

        # Ensure leading slash
        if not clean_path.startswith("/"):
            clean_path = f"/{clean_path}"

        # Strip trailing slash unless root path '/'
        if len(clean_path) > 1 and clean_path.endswith("/"):
            clean_path = clean_path.rstrip("/")

        return f"{clean_method} {clean_path}"

    @classmethod
    def parse_and_normalize_operation(cls, op: str) -> str:
        """Normalize an operation token.

        If the token consists of an HTTP verb followed by a path, normalizes both components.
        Otherwise strips surrounding whitespace.

        Args:
            op: Operation string token.

        Returns:
            Normalized operation string.
        """
        if not isinstance(op, str) or not op.strip():
            raise BaselineValidationError("Operation token must be a non-empty string.")

        parts = op.strip().split(maxsplit=1)
        standard_verbs = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}
        if len(parts) == 2 and parts[0].upper() in standard_verbs:
            return cls.normalize_operation(parts[0], parts[1])

        return op.strip()

    @classmethod
    def normalize_sequence(cls, sequence: list[str]) -> list[str]:
        """Normalize every operation in a sequence.

        Args:
            sequence: List of operation strings.

        Returns:
            List of normalized operation strings.
        """
        cls.validate_sequence(sequence)
        return [cls.parse_and_normalize_operation(op) for op in sequence]

    @classmethod
    def normalize_sequences(cls, sequences: list[list[str]]) -> list[list[str]]:
        """Normalize every sequence in a list of sequences.

        Args:
            sequences: List of operation sequences.

        Returns:
            List of normalized sequences.
        """
        cls.validate_clean_sequences(sequences)
        return [cls.normalize_sequence(seq) for seq in sequences]

    # -------------------------------------------------------------------------
    # Common Validation
    # -------------------------------------------------------------------------

    @staticmethod
    def validate_sequence(sequence: Any, allow_empty: bool = True) -> None:
        """Validate that input sequence is a valid list of non-empty strings.

        Args:
            sequence: Object to validate.
            allow_empty: Whether an empty list is allowed.

        Raises:
            TypeError: If sequence is not a list or elements are not strings.
            BaselineValidationError: If elements are empty or if empty sequence is disallowed.
        """
        if not isinstance(sequence, list):
            raise TypeError(f"Sequence must be a list of strings, got {type(sequence).__name__}.")

        if not allow_empty and len(sequence) == 0:
            raise BaselineValidationError("Sequence cannot be empty.")

        for idx, item in enumerate(sequence):
            if not isinstance(item, str):
                raise TypeError(
                    f"Sequence item at index {idx} must be a str, got {type(item).__name__}."
                )
            if not item.strip():
                raise BaselineValidationError(
                    f"Sequence item at index {idx} cannot be an empty or whitespace-only string."
                )

    @classmethod
    def validate_clean_sequences(cls, clean_sequences: Any) -> None:
        """Validate clean sequences dataset for fitting.

        Args:
            clean_sequences: Dataset of clean sequences.

        Raises:
            TypeError: If clean_sequences is not a list or elements are not lists of strings.
            BaselineValidationError: If clean_sequences is empty or contains invalid items.
        """
        if not isinstance(clean_sequences, list):
            raise TypeError(
                f"clean_sequences must be a list of sequences, got {type(clean_sequences).__name__}."
            )
        if len(clean_sequences) == 0:
            raise BaselineValidationError("clean_sequences cannot be empty.")

        for idx, seq in enumerate(clean_sequences):
            if not isinstance(seq, list):
                raise TypeError(
                    f"clean_sequences[{idx}] must be a list of strings, got {type(seq).__name__}."
                )
            cls.validate_sequence(seq, allow_empty=True)

    @staticmethod
    def validate_threshold(threshold: float) -> None:
        """Validate the decision threshold for prediction.

        Args:
            threshold: Numerical decision threshold.

        Raises:
            TypeError: If threshold is not a float or integer (excluding bool).
            BaselineValidationError: If threshold is NaN or infinite.
        """
        if isinstance(threshold, bool) or not isinstance(threshold, (int, float)):
            raise TypeError(f"Threshold must be a float or int, got {type(threshold).__name__}.")
        if math.isnan(threshold):
            raise BaselineValidationError("Threshold cannot be NaN.")
        if math.isinf(threshold):
            raise BaselineValidationError("Threshold cannot be infinite.")

    def check_is_fitted(self) -> None:
        """Ensure the baseline has been fitted before scoring or prediction.

        Raises:
            BaselineNotFittedError: If model is not fitted.
        """
        if not self._is_fitted:
            raise BaselineNotFittedError(
                f"{self.__class__.__name__} instance is not fitted yet. "
                "Call 'fit' with clean sequences before scoring or predicting."
            )

    # -------------------------------------------------------------------------
    # Abstract Interface
    # -------------------------------------------------------------------------

    @abc.abstractmethod
    def fit(self, clean_sequences: list[list[str]]) -> None:
        """Fit the baseline model on normal sequence traces.

        Args:
            clean_sequences: List of clean API operation sequences.
        """
        ...

    @abc.abstractmethod
    def score_sequence(self, sequence: list[str]) -> float:
        """Compute the anomaly score for a given sequence.

        Args:
            sequence: Sequence of API operations.

        Returns:
            Anomaly score as a float. Higher values indicate higher anomaly likelihood.
        """
        ...

    def predict(self, sequence: list[str], threshold: float) -> bool:
        """Predict whether a sequence is anomalous based on a score threshold.

        Args:
            sequence: Sequence of API operations.
            threshold: Decision threshold.

        Returns:
            True if score >= threshold (anomalous), False otherwise (normal).
        """
        self.check_is_fitted()
        self.validate_threshold(threshold)
        self.validate_sequence(sequence)
        score = self.score_sequence(sequence)
        return score >= threshold

    # -------------------------------------------------------------------------
    # Serialization
    # -------------------------------------------------------------------------

    def _get_state(self) -> dict[str, Any]:
        """Return model internal state for serialization.

        Subclasses may override this method to provide custom state extraction.
        """
        return copy.deepcopy(self._state)

    def _set_state(self, state: dict[str, Any]) -> None:
        """Restore model internal state from serialized data.

        Subclasses may override this method to provide custom state restoration.
        """
        self._state = copy.deepcopy(state)

    def to_dict(self) -> dict[str, Any]:
        """Export model hyperparameters and fitted state to a dictionary.

        Returns:
            Dictionary suitable for JSON serialization.
        """
        return {
            "class_name": self.__class__.__name__,
            "is_fitted": self._is_fitted,
            "params": copy.deepcopy(self._params),
            "state": self._get_state(),
        }

    @classmethod
    def _all_subclasses(cls) -> list[type[BaseSequenceBaseline]]:
        """Recursively collect all known subclasses of BaseSequenceBaseline."""
        subclasses: list[type[BaseSequenceBaseline]] = []
        for sub in cls.__subclasses__():
            subclasses.append(sub)
            subclasses.extend(sub._all_subclasses())
        return subclasses

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        """Reconstruct model instance from a serialized dictionary.

        Args:
            data: Serialized dictionary from `to_dict()`.

        Returns:
            Reconstructed model instance.

        Raises:
            BaselineValidationError: If data format is invalid or target class cannot be resolved.
        """
        if not isinstance(data, dict):
            raise BaselineValidationError(
                f"Expected dict for from_dict, got {type(data).__name__}."
            )

        target_cls: type[Any] = cls
        if inspect.isabstract(cls) or cls is BaseSequenceBaseline:
            class_name = data.get("class_name")
            matched_cls = None
            for sub in BaseSequenceBaseline._all_subclasses():
                if sub.__name__ == class_name and not inspect.isabstract(sub):
                    matched_cls = sub
                    break
            if matched_cls is None:
                raise BaselineValidationError(
                    f"Cannot instantiate abstract class or unknown subclass '{class_name}'."
                )
            target_cls = matched_cls

        params = data.get("params", {})
        instance = target_cls(**params)
        instance._is_fitted = bool(data.get("is_fitted", False))
        instance._set_state(data.get("state", {}))
        return instance

    def save(self, path: str | Path) -> None:
        """Serialize and save the model to disk.

        If path ends with '.json', uses JSON serialization via `to_dict()`.
        Otherwise, uses pickle binary serialization.

        Args:
            path: Destination file path.
        """
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)

        if destination.suffix.lower() == ".json":
            payload = self.to_dict()
            with open(destination, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
        else:
            with open(destination, "wb") as f:
                pickle.dump(self, f, protocol=pickle.HIGHEST_PROTOCOL)

    @classmethod
    def load(cls, path: str | Path) -> Self:
        """Load and deserialize a model from disk.

        Args:
            path: Source file path.

        Returns:
            Restored model instance conforming to cls.

        Raises:
            FileNotFoundError: If the file does not exist.
            TypeError: If the deserialized object is not an instance of cls.
        """
        source = Path(path)
        if not source.is_file():
            raise FileNotFoundError(f"Model file not found at: {source}")

        if source.suffix.lower() == ".json":
            with open(source, "r", encoding="utf-8") as f:
                data = json.load(f)
            return cls.from_dict(data)

        with open(source, "rb") as f:
            obj = pickle.load(f)

        if not isinstance(obj, cls):
            raise TypeError(
                f"Deserialized object type {type(obj).__name__} does not match expected {cls.__name__}."
            )
        return obj
