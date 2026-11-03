"""Unit tests for sequence baseline interfaces and base abstractions.

Verifies:
- SequenceBaselineProtocol runtime checkability and conformance.
- BaseSequenceBaseline validation methods, input normalization, and predict logic.
- Model serialization (save and load) in both JSON and pickle formats.
- Standard interface enforcement across three distinct baseline algorithms.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from services.evaluation.baselines.interfaces import (
    BaselineNotFittedError,
    BaselineValidationError,
    BaseSequenceBaseline,
    SequenceBaselineProtocol,
)

# =============================================================================
# Test Helper Classes
# =============================================================================


class DummyBaseline(BaseSequenceBaseline):
    """Minimal concrete implementation of BaseSequenceBaseline for testing."""

    def __init__(self, multiplier: float = 1.0, **params: Any) -> None:
        super().__init__(multiplier=multiplier, **params)
        self.multiplier = multiplier

    def fit(self, clean_sequences: list[list[str]]) -> None:
        self.validate_clean_sequences(clean_sequences)
        normalized = self.normalize_sequences(clean_sequences)
        self._state["seen_operations"] = sorted({op for seq in normalized for op in seq})
        self._is_fitted = True

    def score_sequence(self, sequence: list[str]) -> float:
        self.check_is_fitted()
        self.validate_sequence(sequence)
        normalized = self.normalize_sequence(sequence)
        seen = set(self._state.get("seen_operations", []))
        if not normalized:
            return 0.0
        unseen_count = sum(1 for op in normalized if op not in seen)
        return (unseen_count / len(normalized)) * self.multiplier


# =============================================================================
# Protocol Conformance Tests
# =============================================================================


def test_protocol_is_runtime_checkable() -> None:
    """SequenceBaselineProtocol must be runtime checkable."""

    class ConformingIndependentClass:
        def fit(self, clean_sequences: list[list[str]]) -> None:
            pass

        def score_sequence(self, sequence: list[str]) -> float:
            return 0.0

        def predict(self, sequence: list[str], threshold: float) -> bool:
            return False

    instance = ConformingIndependentClass()
    assert isinstance(instance, SequenceBaselineProtocol)
    assert issubclass(ConformingIndependentClass, SequenceBaselineProtocol)


def test_base_subclass_conforms_to_protocol() -> None:
    """Subclasses of BaseSequenceBaseline must conform to SequenceBaselineProtocol."""
    model = DummyBaseline()
    assert isinstance(model, SequenceBaselineProtocol)
    assert issubclass(DummyBaseline, SequenceBaselineProtocol)


def test_protocol_rejects_missing_methods() -> None:
    """Classes missing any required method must fail protocol check."""

    class MissingFit:
        def score_sequence(self, sequence: list[str]) -> float:
            return 0.0

        def predict(self, sequence: list[str], threshold: float) -> bool:
            return False

    class MissingScore:
        def fit(self, clean_sequences: list[list[str]]) -> None:
            pass

        def predict(self, sequence: list[str], threshold: float) -> bool:
            return False

    class MissingPredict:
        def fit(self, clean_sequences: list[list[str]]) -> None:
            pass

        def score_sequence(self, sequence: list[str]) -> float:
            return 0.0

    assert not isinstance(MissingFit(), SequenceBaselineProtocol)
    assert not isinstance(MissingScore(), SequenceBaselineProtocol)
    assert not isinstance(MissingPredict(), SequenceBaselineProtocol)


# =============================================================================
# Base Class Instantiation Tests
# =============================================================================


def test_base_class_cannot_be_instantiated_directly() -> None:
    """BaseSequenceBaseline is an ABC and cannot be directly instantiated."""
    with pytest.raises(TypeError, match="Can't instantiate abstract class"):
        BaseSequenceBaseline()  # type: ignore[abstract]


def test_subclass_missing_abstract_methods_cannot_be_instantiated() -> None:
    """Subclasses that do not implement abstract methods cannot be instantiated."""

    class IncompleteBaseline(BaseSequenceBaseline):
        pass

    with pytest.raises(TypeError, match="Can't instantiate abstract class"):
        IncompleteBaseline()  # type: ignore[abstract]


# =============================================================================
# Input Normalization Tests
# =============================================================================


@pytest.mark.parametrize(
    ("method", "path_template", "expected"),
    [
        ("post", " /orders/{id}/ ", "POST /orders/{id}"),
        ("GET", "/", "GET /"),
        ("delete", "///users//123//", "DELETE /users/123"),
        ("PUT", "cart/items", "PUT /cart/items"),
        ("patch", "/api/v1/orders/{order_id}/cancel/", "PATCH /api/v1/orders/{order_id}/cancel"),
        ("HEAD", "///", "HEAD /"),
    ],
)
def test_normalize_operation_success(method: str, path_template: str, expected: str) -> None:
    """normalize_operation produces standard 'METHOD /path' format."""
    assert BaseSequenceBaseline.normalize_operation(method, path_template) == expected


@pytest.mark.parametrize(
    ("method", "path_template"),
    [
        ("", "/orders"),
        ("   ", "/orders"),
        ("POST", ""),
        ("POST", "   "),
        (None, "/orders"),  # type: ignore[arg-type]
        ("GET", None),  # type: ignore[arg-type]
    ],
)
def test_normalize_operation_invalid_inputs(method: Any, path_template: Any) -> None:
    """normalize_operation raises BaselineValidationError on invalid strings."""
    with pytest.raises(BaselineValidationError):
        BaseSequenceBaseline.normalize_operation(method, path_template)


def test_parse_and_normalize_operation() -> None:
    """parse_and_normalize_operation handles both method+path and atomic tokens."""
    assert (
        BaseSequenceBaseline.parse_and_normalize_operation("  post   /orders/{id}/  ")
        == "POST /orders/{id}"
    )
    assert BaseSequenceBaseline.parse_and_normalize_operation("custom_op_name") == "custom_op_name"
    with pytest.raises(BaselineValidationError):
        BaseSequenceBaseline.parse_and_normalize_operation("   ")


def test_normalize_sequence_and_sequences() -> None:
    """normalize_sequence and normalize_sequences normalize batches correctly."""
    seq = ["post /orders/", "get /orders/{id}/", "custom_step"]
    expected = ["POST /orders", "GET /orders/{id}", "custom_step"]
    assert BaseSequenceBaseline.normalize_sequence(seq) == expected

    batch = [
        ["post /cart/items/"],
        ["get /cart", "post /orders"],
    ]
    expected_batch = [
        ["POST /cart/items"],
        ["GET /cart", "POST /orders"],
    ]
    assert BaseSequenceBaseline.normalize_sequences(batch) == expected_batch


# =============================================================================
# Validation Tests
# =============================================================================


def test_validate_sequence_valid() -> None:
    """validate_sequence passes on valid list of strings."""
    BaseSequenceBaseline.validate_sequence(["POST /orders", "GET /orders/1"])
    BaseSequenceBaseline.validate_sequence([], allow_empty=True)


def test_validate_sequence_disallow_empty() -> None:
    """validate_sequence raises when allow_empty is False and sequence is empty."""
    with pytest.raises(BaselineValidationError, match="cannot be empty"):
        BaseSequenceBaseline.validate_sequence([], allow_empty=False)


@pytest.mark.parametrize(
    "invalid_seq",
    [
        "not a list",
        123,
        None,
        {"op": "POST /orders"},
        ("POST /orders",),
    ],
)
def test_validate_sequence_type_errors(invalid_seq: Any) -> None:
    """validate_sequence raises TypeError for non-list inputs."""
    with pytest.raises(TypeError, match="Sequence must be a list"):
        BaseSequenceBaseline.validate_sequence(invalid_seq)


@pytest.mark.parametrize(
    "invalid_items",
    [
        [1, 2, 3],
        ["POST /orders", None],
        ["POST /orders", 42],
    ],
)
def test_validate_sequence_item_type_errors(invalid_items: Any) -> None:
    """validate_sequence raises TypeError if items are not strings."""
    with pytest.raises(TypeError, match="must be a str"):
        BaseSequenceBaseline.validate_sequence(invalid_items)


@pytest.mark.parametrize(
    "empty_items",
    [
        [""],
        ["   "],
        ["POST /orders", ""],
    ],
)
def test_validate_sequence_empty_item_errors(empty_items: list[str]) -> None:
    """validate_sequence raises BaselineValidationError for empty string items."""
    with pytest.raises(BaselineValidationError, match="cannot be an empty"):
        BaseSequenceBaseline.validate_sequence(empty_items)


def test_validate_clean_sequences_valid() -> None:
    """validate_clean_sequences passes on valid sequence datasets."""
    BaseSequenceBaseline.validate_clean_sequences(
        [
            ["POST /orders"],
            ["POST /cart", "POST /checkout"],
        ]
    )


def test_validate_clean_sequences_empty_dataset() -> None:
    """validate_clean_sequences raises when dataset is empty."""
    with pytest.raises(BaselineValidationError, match="clean_sequences cannot be empty"):
        BaseSequenceBaseline.validate_clean_sequences([])


@pytest.mark.parametrize(
    "invalid_dataset",
    [
        "not a list",
        None,
        123,
    ],
)
def test_validate_clean_sequences_type_errors(invalid_dataset: Any) -> None:
    """validate_clean_sequences raises TypeError for non-list dataset."""
    with pytest.raises(TypeError, match="must be a list of sequences"):
        BaseSequenceBaseline.validate_clean_sequences(invalid_dataset)


@pytest.mark.parametrize(
    "invalid_elements",
    [
        ["POST /orders"],  # list of strings, not list of lists
        [1, 2],
        [["POST /orders"], "not a list"],
    ],
)
def test_validate_clean_sequences_element_errors(invalid_elements: Any) -> None:
    """validate_clean_sequences raises TypeError if sequences are not lists."""
    with pytest.raises(TypeError, match="must be a list of strings"):
        BaseSequenceBaseline.validate_clean_sequences(invalid_elements)


@pytest.mark.parametrize("valid_threshold", [0.0, 0.5, 1.0, -10.0, 100, 0])
def test_validate_threshold_valid(valid_threshold: float) -> None:
    """validate_threshold accepts valid numbers."""
    BaseSequenceBaseline.validate_threshold(valid_threshold)


@pytest.mark.parametrize(
    "invalid_type",
    [
        "0.5",
        None,
        True,  # bool is not a valid float threshold
        False,
        [0.5],
    ],
)
def test_validate_threshold_type_errors(invalid_type: Any) -> None:
    """validate_threshold rejects non-numeric or boolean values."""
    with pytest.raises(TypeError, match="Threshold must be a float or int"):
        BaseSequenceBaseline.validate_threshold(invalid_type)


@pytest.mark.parametrize("invalid_value", [float("nan"), float("inf"), float("-inf")])
def test_validate_threshold_value_errors(invalid_value: float) -> None:
    """validate_threshold rejects NaN and infinite values."""
    with pytest.raises(BaselineValidationError):
        BaseSequenceBaseline.validate_threshold(invalid_value)


# =============================================================================
# Fitted State & Prediction Tests
# =============================================================================


def test_unfitted_model_raises_on_scoring_or_prediction() -> None:
    """Scoring or predicting on an unfitted model raises BaselineNotFittedError."""
    model = DummyBaseline()
    assert not model.is_fitted

    with pytest.raises(BaselineNotFittedError, match="not fitted yet"):
        model.score_sequence(["POST /orders"])

    with pytest.raises(BaselineNotFittedError, match="not fitted yet"):
        model.predict(["POST /orders"], threshold=0.5)


def test_fit_and_predict_threshold_logic() -> None:
    """Fitted model computes score and predicts True if score >= threshold."""
    model = DummyBaseline(multiplier=1.0)
    clean_data = [
        ["POST /orders", "POST /orders/1/payment"],
        ["POST /orders", "POST /orders/2/cancel"],
    ]
    model.fit(clean_data)
    assert model.is_fitted

    # Sequence with 0 unseen operations: score = 0.0
    normal_seq = ["POST /orders", "POST /orders/1/payment"]
    assert model.score_sequence(normal_seq) == 0.0
    assert not model.predict(normal_seq, threshold=0.1)

    # Sequence with 1 unseen operation out of 2: score = 0.5
    anomalous_seq = ["POST /orders", "DELETE /orders/99"]
    assert model.score_sequence(anomalous_seq) == 0.5
    assert model.predict(anomalous_seq, threshold=0.5)
    assert not model.predict(anomalous_seq, threshold=0.51)


def test_predict_validates_inputs() -> None:
    """predict validates sequence and threshold arguments."""
    model = DummyBaseline()
    model.fit([["POST /orders"]])

    with pytest.raises(TypeError):
        model.predict(["POST /orders"], threshold="invalid")  # type: ignore[arg-type]

    with pytest.raises(TypeError):
        model.predict("not a sequence", threshold=0.5)  # type: ignore[arg-type]


# =============================================================================
# Model Serialization Tests (save & load)
# =============================================================================


def test_save_and_load_json(tmp_path: Path) -> None:
    """Model can be saved and loaded from JSON, preserving params, state, and behavior."""
    model = DummyBaseline(multiplier=2.0)
    model.fit([["POST /cart", "POST /checkout"]])

    save_path = tmp_path / "models" / "sub" / "dummy_model.json"
    model.save(save_path)
    assert save_path.is_file()

    loaded = DummyBaseline.load(save_path)
    assert isinstance(loaded, DummyBaseline)
    assert loaded.is_fitted
    assert loaded.multiplier == 2.0
    assert loaded.params == {"multiplier": 2.0}

    seq = ["POST /cart", "DELETE /orders"]
    assert loaded.score_sequence(seq) == model.score_sequence(seq)


def test_save_and_load_pickle(tmp_path: Path) -> None:
    """Model can be saved and loaded via pickle binary format."""
    model = DummyBaseline(multiplier=1.5)
    model.fit([["GET /items", "POST /orders"]])

    save_path = tmp_path / "dummy_model.pkl"
    model.save(save_path)
    assert save_path.is_file()

    loaded = DummyBaseline.load(save_path)
    assert isinstance(loaded, DummyBaseline)
    assert loaded.is_fitted
    assert loaded.multiplier == 1.5

    seq = ["GET /items"]
    assert loaded.score_sequence(seq) == model.score_sequence(seq)


def test_load_from_base_class_json(tmp_path: Path) -> None:
    """BaseSequenceBaseline.load(json_path) instantiates the proper subclass."""
    model = DummyBaseline(multiplier=3.0)
    model.fit([["POST /orders"]])

    save_path = tmp_path / "model.json"
    model.save(save_path)

    loaded = BaseSequenceBaseline.load(save_path)
    assert isinstance(loaded, DummyBaseline)
    assert loaded.multiplier == 3.0


def test_load_non_existent_file() -> None:
    """load raises FileNotFoundError if target path does not exist."""
    with pytest.raises(FileNotFoundError, match="Model file not found"):
        DummyBaseline.load("non_existent_path.json")


def test_load_pickle_type_mismatch(tmp_path: Path) -> None:
    """load raises TypeError if pickle content does not match expected subclass."""
    import pickle

    foreign_path = tmp_path / "foreign.pkl"
    with open(foreign_path, "wb") as f:
        pickle.dump({"not": "a model"}, f)

    with pytest.raises(TypeError, match="does not match expected DummyBaseline"):
        DummyBaseline.load(foreign_path)


# =============================================================================
# Acceptance Criteria: Enforce standard interface across three baselines
# =============================================================================


class MarkovSequenceBaseline(BaseSequenceBaseline):
    """First baseline algorithm: First-order Markov sequence transition model."""

    def __init__(self, smoothing: float = 0.01, **params: Any) -> None:
        super().__init__(smoothing=smoothing, **params)
        self.smoothing = smoothing

    def fit(self, clean_sequences: list[list[str]]) -> None:
        self.validate_clean_sequences(clean_sequences)
        normalized = self.normalize_sequences(clean_sequences)
        transitions: dict[str, dict[str, int]] = {}
        for seq in normalized:
            for i in range(len(seq) - 1):
                src, dst = seq[i], seq[i + 1]
                transitions.setdefault(src, {}).setdefault(dst, 0)
                transitions[src][dst] += 1
        self._state["transitions"] = transitions
        self._is_fitted = True

    def score_sequence(self, sequence: list[str]) -> float:
        self.check_is_fitted()
        self.validate_sequence(sequence)
        normalized = self.normalize_sequence(sequence)
        if len(normalized) < 2:
            return 0.0
        transitions = self._state.get("transitions", {})
        anomalous_transitions = 0
        total_transitions = len(normalized) - 1
        for i in range(total_transitions):
            src, dst = normalized[i], normalized[i + 1]
            if src not in transitions or dst not in transitions[src]:
                anomalous_transitions += 1
        return anomalous_transitions / total_transitions


class NGramSequenceBaseline(BaseSequenceBaseline):
    """Second baseline algorithm: N-Gram sequence frequency baseline."""

    def __init__(self, n: int = 2, **params: Any) -> None:
        super().__init__(n=n, **params)
        self.n = n

    def fit(self, clean_sequences: list[list[str]]) -> None:
        self.validate_clean_sequences(clean_sequences)
        normalized = self.normalize_sequences(clean_sequences)
        seen_ngrams: set[tuple[str, ...]] = set()
        for seq in normalized:
            if len(seq) >= self.n:
                for i in range(len(seq) - self.n + 1):
                    seen_ngrams.add(tuple(seq[i : i + self.n]))
        self._state["seen_ngrams"] = [list(ng) for ng in seen_ngrams]
        self._is_fitted = True

    def score_sequence(self, sequence: list[str]) -> float:
        self.check_is_fitted()
        self.validate_sequence(sequence)
        normalized = self.normalize_sequence(sequence)
        if len(normalized) < self.n:
            return 0.0
        seen_ngrams = {tuple(ng) for ng in self._state.get("seen_ngrams", [])}
        total_ngrams = len(normalized) - self.n + 1
        unseen_count = 0
        for i in range(total_ngrams):
            ngram = tuple(normalized[i : i + self.n])
            if ngram not in seen_ngrams:
                unseen_count += 1
        return unseen_count / total_ngrams


class FrequencySequenceBaseline(BaseSequenceBaseline):
    """Third baseline algorithm: Operation frequency / occurrence baseline."""

    def __init__(self, min_freq: int = 1, **params: Any) -> None:
        super().__init__(min_freq=min_freq, **params)
        self.min_freq = min_freq

    def fit(self, clean_sequences: list[list[str]]) -> None:
        self.validate_clean_sequences(clean_sequences)
        normalized = self.normalize_sequences(clean_sequences)
        counts: dict[str, int] = {}
        for seq in normalized:
            for op in seq:
                counts[op] = counts.get(op, 0) + 1
        self._state["counts"] = counts
        self._is_fitted = True

    def score_sequence(self, sequence: list[str]) -> float:
        self.check_is_fitted()
        self.validate_sequence(sequence)
        normalized = self.normalize_sequence(sequence)
        if not normalized:
            return 0.0
        counts = self._state.get("counts", {})
        unseen = sum(1 for op in normalized if counts.get(op, 0) < self.min_freq)
        return unseen / len(normalized)


@pytest.mark.parametrize(
    "baseline_cls",
    [
        MarkovSequenceBaseline,
        NGramSequenceBaseline,
        FrequencySequenceBaseline,
    ],
)
def test_all_three_baselines_conform_to_protocol_and_contract(
    baseline_cls: type[BaseSequenceBaseline], tmp_path: Path
) -> None:
    """Verify that all three baseline algorithms enforce the standard interface."""
    # 1. Structural and type protocol conformance
    assert issubclass(baseline_cls, SequenceBaselineProtocol)
    baseline = baseline_cls()
    assert isinstance(baseline, SequenceBaselineProtocol)

    # 2. Fitting on clean traces
    clean_traces = [
        ["POST /orders", "POST /orders/{id}/payment", "POST /orders/{id}/ship"],
        ["POST /orders", "POST /orders/{id}/cancel"],
    ]
    baseline.fit(clean_traces)
    assert baseline.is_fitted

    # 3. Anomaly scoring & prediction
    normal_seq = ["POST /orders", "POST /orders/{id}/payment"]
    normal_score = baseline.score_sequence(normal_seq)
    assert isinstance(normal_score, float)
    assert 0.0 <= normal_score <= 1.0
    assert not baseline.predict(normal_seq, threshold=0.99)

    anomalous_seq = ["POST /orders/{id}/ship", "DELETE /unknown"]
    anom_score = baseline.score_sequence(anomalous_seq)
    assert isinstance(anom_score, float)
    assert anom_score > 0.0

    # 4. Model serialization round-trip
    model_path = tmp_path / f"{baseline_cls.__name__}.json"
    baseline.save(model_path)
    loaded = baseline_cls.load(model_path)
    assert loaded.is_fitted
    assert loaded.score_sequence(normal_seq) == normal_score
    assert loaded.score_sequence(anomalous_seq) == anom_score
