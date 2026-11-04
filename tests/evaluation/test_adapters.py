"""Unit tests for StreamDetectorProtocol and baseline adapters.

Verifies:
- Protocol runtime checkability for all 4 stream detector adapters.
- StraphDetectorAdapter processes event stream and emits schema-compliant alerts.
- NGramDetectorAdapter processes legal streams without false positives and flags anomalies.
- MarkovDetectorAdapter processes legal streams without false positives and flags anomalies.
- TraceInvariantDetectorAdapter detects illegal transitions and repeated single-use operations.
- All 4 adapters process identical event streams and output standardized alerts conforming
  strictly to contracts/alert.schema.json.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from services.detector.worker import DetectorWorker
from services.evaluation.adapters import (
    MarkovDetectorAdapter,
    NGramDetectorAdapter,
    StraphDetectorAdapter,
    StreamDetectorProtocol,
    TraceInvariantDetectorAdapter,
)
from services.evaluation.baselines.markov import FirstOrderMarkovBaseline
from services.evaluation.baselines.ngram import NGramBaseline
from services.evaluation.baselines.trace_invariant import TraceInvariantBaseline
from tests.detector.fixtures.tc1_legal_path import get_tc1_events
from tests.detector.fixtures.tc2_skipped_payment import get_tc2_events
from tests.detector.fixtures.tc4_double_refund import get_tc4_events

ROOT: Path = Path(__file__).resolve().parents[2]
ALERT_SCHEMA_PATH: Path = ROOT / "contracts" / "alert.schema.json"

CLEAN_TRAINING_SEQUENCE: list[str] = [
    "POST /cart/items",
    "POST /orders",
    "PUT /orders/{id}/address",
    "POST /orders/{id}/payment",
    "POST /payments/webhook",
    "POST /orders/{id}/fulfill",
    "POST /orders/{id}/ship",
]

REFUND_TRAINING_SEQUENCE: list[str] = [
    "POST /cart/items",
    "POST /orders",
    "PUT /orders/{id}/address",
    "POST /orders/{id}/payment",
    "POST /payments/webhook",
    "POST /orders/{id}/refund",
]


@pytest.fixture
def alert_validator() -> Draft202012Validator:
    """Provide schema validator for contracts/alert.schema.json."""
    schema: dict[str, Any] = json.loads(ALERT_SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


@pytest.fixture
def fitted_ngram() -> NGramBaseline:
    """Provide a fitted NGramBaseline."""
    baseline = NGramBaseline(n=2, alpha=0.01)
    baseline.fit([CLEAN_TRAINING_SEQUENCE, REFUND_TRAINING_SEQUENCE])
    return baseline


@pytest.fixture
def fitted_markov() -> FirstOrderMarkovBaseline:
    """Provide a fitted FirstOrderMarkovBaseline."""
    baseline = FirstOrderMarkovBaseline(epsilon=1e-4)
    baseline.fit([CLEAN_TRAINING_SEQUENCE, REFUND_TRAINING_SEQUENCE])
    return baseline


@pytest.fixture
def fitted_trace_invariant() -> TraceInvariantBaseline:
    """Provide a fitted TraceInvariantBaseline."""
    baseline = TraceInvariantBaseline()
    baseline.fit([CLEAN_TRAINING_SEQUENCE, REFUND_TRAINING_SEQUENCE])
    return baseline


def test_protocol_conformance(
    fitted_ngram: NGramBaseline,
    fitted_markov: FirstOrderMarkovBaseline,
    fitted_trace_invariant: TraceInvariantBaseline,
) -> None:
    """All 4 adapters must satisfy StreamDetectorProtocol."""
    worker = DetectorWorker()
    straph_adapter = StraphDetectorAdapter(worker)
    ngram_adapter = NGramDetectorAdapter(fitted_ngram, threshold=1.0)
    markov_adapter = MarkovDetectorAdapter(fitted_markov, threshold=1.0)
    ti_adapter = TraceInvariantDetectorAdapter(fitted_trace_invariant)

    assert isinstance(straph_adapter, StreamDetectorProtocol)
    assert isinstance(ngram_adapter, StreamDetectorProtocol)
    assert isinstance(markov_adapter, StreamDetectorProtocol)
    assert isinstance(ti_adapter, StreamDetectorProtocol)


def test_straph_adapter_tc1_tc2(alert_validator: Draft202012Validator) -> None:
    """StraphDetectorAdapter processes legal stream cleanly and flags TC2 skipped payment."""
    adapter = StraphDetectorAdapter(DetectorWorker())

    # Legal sequence -> zero alerts
    alerts_tc1 = adapter.process_event_stream(get_tc1_events())
    assert alerts_tc1 == []

    # Skipped payment -> 1 alert
    adapter_tc2 = StraphDetectorAdapter(DetectorWorker())
    alerts_tc2 = adapter_tc2.process_event_stream(get_tc2_events())
    assert len(alerts_tc2) == 1
    alert = alerts_tc2[0]
    assert alert["label"] == "SKIPPED"
    assert alert["rule"] == "R2"
    assert alert["severity"] == "accepted"
    errors = list(alert_validator.iter_errors(alert))
    assert errors == []


def test_ngram_adapter_legal_and_abusive(
    fitted_ngram: NGramBaseline,
    alert_validator: Draft202012Validator,
) -> None:
    """NGramDetectorAdapter raises no false positives on TC1 and catches TC2 anomaly."""
    # TC1 branching score reaches ~0.72; unseen transitions score > 5.0. Set threshold to 1.5.
    adapter = NGramDetectorAdapter(fitted_ngram, threshold=1.5)

    alerts_tc1 = adapter.process_event_stream(get_tc1_events())
    assert alerts_tc1 == []

    # In TC2, address -> fulfill is an unseen bigram
    alerts_tc2 = adapter.process_event_stream(get_tc2_events())
    assert len(alerts_tc2) >= 1
    for alert in alerts_tc2:
        errors = list(alert_validator.iter_errors(alert))
        assert errors == []
        assert alert["label"] in {"SKIPPED", "OUT_OF_ORDER"}


def test_markov_adapter_legal_and_abusive(
    fitted_markov: FirstOrderMarkovBaseline,
    alert_validator: Draft202012Validator,
) -> None:
    """MarkovDetectorAdapter raises no false positives on TC1 and catches TC2 anomaly."""
    # In clean sequences, address -> fulfill has 0 transition probability
    clean_score = fitted_markov.score_sequence(CLEAN_TRAINING_SEQUENCE)
    adapter = MarkovDetectorAdapter(fitted_markov, threshold=clean_score + 1.0)

    alerts_tc1 = adapter.process_event_stream(get_tc1_events())
    assert alerts_tc1 == []

    alerts_tc2 = adapter.process_event_stream(get_tc2_events())
    assert len(alerts_tc2) >= 1
    for alert in alerts_tc2:
        errors = list(alert_validator.iter_errors(alert))
        assert errors == []
        assert alert["label"] == "OUT_OF_ORDER"
        assert alert["rule"] == "R3"


def test_trace_invariant_adapter_legal_and_abusive(
    fitted_trace_invariant: TraceInvariantBaseline,
    alert_validator: Draft202012Validator,
) -> None:
    """TraceInvariantDetectorAdapter validates normal paths and catches both illegal pairs and replays."""
    adapter = TraceInvariantDetectorAdapter(fitted_trace_invariant)

    alerts_tc1 = adapter.process_event_stream(get_tc1_events())
    assert alerts_tc1 == []

    alerts_tc2 = adapter.process_event_stream(get_tc2_events())
    assert len(alerts_tc2) == 1
    alert_tc2 = alerts_tc2[0]
    assert alert_tc2["label"] == "OUT_OF_ORDER"
    assert alert_tc2["rule"] == "R3"
    errors = list(alert_validator.iter_errors(alert_tc2))
    assert errors == []

    # TC4 has duplicate refund -> replayed single use
    adapter_tc4 = TraceInvariantDetectorAdapter(fitted_trace_invariant)
    alerts_tc4 = adapter_tc4.process_event_stream(get_tc4_events())
    assert len(alerts_tc4) >= 1
    assert any(a["label"] == "REPLAYED" and a["rule"] == "R1" for a in alerts_tc4)
    for a in alerts_tc4:
        errors = list(alert_validator.iter_errors(a))
        assert errors == []


def test_all_adapters_process_same_stream_standardized(
    fitted_ngram: NGramBaseline,
    fitted_markov: FirstOrderMarkovBaseline,
    fitted_trace_invariant: TraceInvariantBaseline,
    alert_validator: Draft202012Validator,
) -> None:
    """Verify all 4 adapters process the exact same event list and return standardized alert dicts."""
    events = get_tc2_events()

    adapters: list[StreamDetectorProtocol] = [
        StraphDetectorAdapter(DetectorWorker()),
        NGramDetectorAdapter(fitted_ngram, threshold=1.5),
        MarkovDetectorAdapter(fitted_markov, threshold=2.0),
        TraceInvariantDetectorAdapter(fitted_trace_invariant),
    ]

    for adapter in adapters:
        alerts = adapter.process_event_stream(events)
        assert isinstance(alerts, list)
        assert len(alerts) >= 1, f"{adapter.__class__.__name__} failed to detect TC2 anomaly"
        for alert in alerts:
            assert isinstance(alert, dict)
            # Must strictly validate against alert.schema.json
            errors = list(alert_validator.iter_errors(alert))
            assert errors == [], f"{adapter.__class__.__name__} alert invalid: {errors}"
            assert "alert_id" in alert
            assert "event_id" in alert
            assert "timestamp" in alert
            assert "label" in alert
            assert "rule" in alert
            assert "severity" in alert
            assert "resource" in alert
            assert "observed" in alert
