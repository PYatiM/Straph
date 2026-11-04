"""Unit tests for consolidated prototype evaluation runner (Prompt P103)."""

from __future__ import annotations

import pytest

from services.evaluation.run_prototype_eval import (
    build_composite_evaluation_stream,
    format_comparative_markdown_table,
    run_prototype_evaluation,
)


def test_build_composite_evaluation_stream_composition() -> None:
    """Verify composite stream has ~150+ events: 20 legal checkouts + 6 attacks + benign edge cases."""
    events, labels = build_composite_evaluation_stream(num_legal_checkouts=20, seed=42)

    # 20 * 7 (140) + 33 (attacks 1-6) + 11 (benign edge cases) = 184 events
    assert len(events) == 184
    assert len(labels) == 184

    violation_labels = [lbl for lbl in labels if lbl.is_violation]
    assert len(violation_labels) == 6

    expected_attack_scenarios = {
        "ATTACK_1_SKIP_PAYMENT",
        "ATTACK_2_ADDRESS_AFTER_SHIPPING",
        "ATTACK_3_DOUBLE_REFUND",
        "ATTACK_4_BOLA_CANCEL",
        "ATTACK_5_BFLA_FULFILL",
        "ATTACK_6_SPLIT_TOKEN_SKIP",
    }
    assert {lbl.scenario_id for lbl in violation_labels} == expected_attack_scenarios


def test_run_prototype_evaluation_all_four_detectors_and_markdown_output(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Run prototype evaluation, verify markdown table stdout output, and assert Straph 100% recall."""
    results = run_prototype_evaluation(num_legal_checkouts=20, seed=42, print_table=True)

    captured = capsys.readouterr().out
    expected_header = (
        "| Detector | Precision | Recall | F1 Score | FP / 1000 Events | Explanation Match |"
    )
    assert expected_header in captured

    for detector_name in ("Straph", "N-Gram", "Markov", "TraceInvariant"):
        assert detector_name in results
        assert f"| {detector_name} |" in captured

    # Straph must achieve 100% recall, 100% precision, 0 FP/1k, and 100% explanation match
    straph = results["Straph"]
    assert straph["recall"] == pytest.approx(1.0)
    assert straph["precision"] == pytest.approx(1.0)
    assert straph["f1_score"] == pytest.approx(1.0)
    assert straph["false_positives_per_1000_events"] == pytest.approx(0.0)
    assert straph["explanation_correctness_rate"] == pytest.approx(1.0)
    assert straph["tp"] == 6.0
    assert straph["fn"] == 0.0
    assert straph["fp"] == 0.0

    # Sequence baselines cannot detect pure identity/role violations (BOLA/BFLA) or session-split skips
    for baseline_name in ("N-Gram", "Markov", "TraceInvariant"):
        assert results[baseline_name]["recall"] < straph["recall"]


def test_format_comparative_markdown_table_structure() -> None:
    """Verify markdown table formatting function produces well-formed table rows."""
    sample = {
        "Straph": {
            "precision": 1.0,
            "recall": 1.0,
            "f1_score": 1.0,
            "false_positives_per_1000_events": 0.0,
            "explanation_correctness_rate": 1.0,
        }
    }
    table = format_comparative_markdown_table(sample)
    lines = table.splitlines()
    assert len(lines) == 3
    assert lines[0] == (
        "| Detector | Precision | Recall | F1 Score | FP / 1000 Events | Explanation Match |"
    )
    assert "| Straph | 1.000 | 1.000 | 1.000 | 0.00 | 100.0% |" in lines[2]
