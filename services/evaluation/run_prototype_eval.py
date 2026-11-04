"""Consolidated prototype evaluation runner script (Prompt P103).

Generates a composite test stream of 20 legal checkouts, Attacks 1 through 6,
and benign edge cases (~185 events total), benchmarks StraphDetectorAdapter,
NGramDetectorAdapter, MarkovDetectorAdapter, and TraceInvariantDetectorAdapter,
calculates comparative IDS metrics, prints a markdown table to stdout, and
verifies 100% recall on planted Attacks 1-6 for Straph.
"""

from __future__ import annotations

import random
import uuid
from typing import Any

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
from services.evaluation.generators.attack_backward import generate_post_ship_edit_attack
from services.evaluation.generators.attack_bfla import generate_bfla_attack
from services.evaluation.generators.attack_bola import generate_bola_attack
from services.evaluation.generators.attack_replay import generate_double_refund_attack
from services.evaluation.generators.attack_skip import generate_skip_payment_attack
from services.evaluation.generators.attack_split_token import generate_split_token_attack
from services.evaluation.generators.benign_edge_cases import generate_benign_edge_cases
from services.evaluation.label_joiner import join_alerts_with_labels
from services.evaluation.labels import ExpectedLabel, GroundTruthLabel
from services.evaluation.metrics import calculate_metrics
from services.evaluation.replay_runner import ReplayRunner

SCENARIO_LEGAL_CHECKOUT = "LEGAL_CHECKOUT"

CLEAN_TRAINING_SEQUENCES: list[list[str]] = [
    [
        "POST /cart/items",
        "POST /orders",
        "PUT /orders/{id}/address",
        "POST /orders/{id}/payment",
        "POST /payments/webhook",
        "POST /orders/{id}/fulfill",
        "POST /orders/{id}/ship",
    ],
    [
        "POST /cart/items",
        "POST /orders",
        "PUT /orders/{id}/address",
        "POST /orders/{id}/payment",
        "POST /payments/webhook",
        "POST /orders/{id}/fulfill",
        "POST /orders/{id}/ship",
        "POST /orders/{id}/deliver",
        "POST /orders/{id}/refund",
    ],
    [
        "POST /orders",
        "PUT /orders/{id}/address",
        "POST /orders/{id}/cancel",
    ],
]


def _seeded_uuid4(rng: random.Random) -> str:
    """Generate a deterministic RFC-4122 UUID4 string from a seeded RNG."""
    return str(uuid.UUID(int=rng.getrandbits(128), version=4))


def generate_legal_checkout(
    order_id: str,
    seed: int = 42,
) -> tuple[list[dict[str, Any]], list[GroundTruthLabel]]:
    """Generate a 7-event legal checkout path (Cart -> Order -> Address -> Payment -> Webhook -> Fulfill -> Ship)."""
    clean_order_id = order_id.strip()
    rng = random.Random(f"legal_checkout:{clean_order_id}:{seed}")

    customer_sub = f"customer-{clean_order_id}"
    payment_sub = "payment-gateway"
    warehouse_sub = "warehouse-worker-1"

    step_specs: list[tuple[str, str, str, str, int, str, str]] = [
        (customer_sub, "customer", "POST", "/cart/items", 200, "Cart", f"cart-{clean_order_id}"),
        (customer_sub, "customer", "POST", "/orders", 201, "Order", clean_order_id),
        (customer_sub, "customer", "PUT", "/orders/{id}/address", 200, "Order", clean_order_id),
        (customer_sub, "customer", "POST", "/orders/{id}/payment", 200, "Order", clean_order_id),
        (
            payment_sub,
            "payment-gateway",
            "POST",
            "/payments/webhook",
            200,
            "Order",
            clean_order_id,
        ),
        (warehouse_sub, "warehouse", "POST", "/orders/{id}/fulfill", 200, "Order", clean_order_id),
        (warehouse_sub, "warehouse", "POST", "/orders/{id}/ship", 200, "Order", clean_order_id),
    ]

    events: list[dict[str, Any]] = []
    labels: list[GroundTruthLabel] = []

    for idx, (sub, role, method, path_template, status, res_type, res_id) in enumerate(step_specs):
        event_id = _seeded_uuid4(rng)
        events.append(
            {
                "event_id": event_id,
                "ts_received": f"2026-10-29T10:{idx:02d}:00.000Z",
                "ts_completed": f"2026-10-29T10:{idx:02d}:00.050Z",
                "subject": {
                    "issuer": "straph-eval",
                    "sub": sub,
                    "role": role,
                },
                "session_id": f"sess-{sub}",
                "token_id": f"tok-{sub}",
                "operation": {
                    "method": method,
                    "path_template": path_template,
                },
                "resource_type": res_type,
                "resource_id": res_id,
                "status_code": status,
            }
        )
        labels.append(
            GroundTruthLabel(
                event_id=event_id,
                is_violation=False,
                expected_label=ExpectedLabel.BENIGN,
                expected_rule=None,
                missing_ops=[],
                scenario_id=SCENARIO_LEGAL_CHECKOUT,
                seed=seed,
            )
        )

    return events, labels


def build_composite_evaluation_stream(
    *,
    num_legal_checkouts: int = 20,
    seed: int = 42,
) -> tuple[list[dict[str, Any]], list[GroundTruthLabel]]:
    """Build the composite evaluation stream: 20 legal checkouts + Attacks 1-6 + benign edge cases."""
    events: list[dict[str, Any]] = []
    labels: list[GroundTruthLabel] = []

    # 1. 20 legal checkouts (20 * 7 = 140 events)
    for i in range(1, num_legal_checkouts + 1):
        ev, lbl = generate_legal_checkout(order_id=f"legal-{1000 + i}", seed=seed + i)
        events.extend(ev)
        labels.extend(lbl)

    # 2. Attacks 1 through 6 (3 + 8 + 10 + 3 + 6 + 3 = 33 events, 6 violations)
    attack_builders = [
        (generate_skip_payment_attack, "atk1-2001"),
        (generate_post_ship_edit_attack, "atk2-2002"),
        (generate_double_refund_attack, "atk3-2003"),
        (generate_bola_attack, "atk4-2004"),
        (generate_bfla_attack, "atk5-2005"),
        (generate_split_token_attack, "atk6-2006"),
    ]
    for offset, (builder, order_id) in enumerate(attack_builders, start=100):
        ev, lbl = builder(order_id=order_id, seed=seed + offset)
        events.extend(ev)
        labels.extend(lbl)

    # 3. Benign edge cases (11 events)
    edge_ev, edge_lbl = generate_benign_edge_cases(seed=seed + 500)
    events.extend(edge_ev)
    labels.extend(edge_lbl)

    return events, labels


def build_detector_adapters() -> dict[str, StreamDetectorProtocol]:
    """Instantiate and train all 4 detector adapters for comparative evaluation."""
    ngram = NGramBaseline(n=2, alpha=0.01)
    ngram.fit(CLEAN_TRAINING_SEQUENCES)

    markov = FirstOrderMarkovBaseline(epsilon=1e-4)
    markov.fit(CLEAN_TRAINING_SEQUENCES)

    trace_invariant = TraceInvariantBaseline()
    trace_invariant.fit(CLEAN_TRAINING_SEQUENCES)

    return {
        "Straph": StraphDetectorAdapter(DetectorWorker()),
        "N-Gram": NGramDetectorAdapter(ngram, threshold=1.5),
        "Markov": MarkovDetectorAdapter(markov, threshold=2.0),
        "TraceInvariant": TraceInvariantDetectorAdapter(trace_invariant),
    }


def format_comparative_markdown_table(results: dict[str, dict[str, float]]) -> str:
    """Format comparative detector evaluation metrics as a Markdown table."""
    lines: list[str] = [
        "| Detector | Precision | Recall | F1 Score | FP / 1000 Events | Explanation Match |",
        "| :--- | :---: | :---: | :---: | :---: | :---: |",
    ]
    for name, m in results.items():
        lines.append(
            f"| {name} "
            f"| {m['precision']:.3f} "
            f"| {m['recall']:.3f} "
            f"| {m['f1_score']:.3f} "
            f"| {m['false_positives_per_1000_events']:.2f} "
            f"| {m['explanation_correctness_rate']:.1%} |"
        )
    return "\n".join(lines)


def run_prototype_evaluation(
    *,
    num_legal_checkouts: int = 20,
    seed: int = 42,
    print_table: bool = True,
) -> dict[str, dict[str, float]]:
    """Run the comparative prototype evaluation across all 4 detectors.

    Asserts that Straph achieves 100% recall on planted Attacks 1-6.
    """
    events, labels = build_composite_evaluation_stream(
        num_legal_checkouts=num_legal_checkouts,
        seed=seed,
    )
    adapters = build_detector_adapters()
    runner = ReplayRunner()

    results: dict[str, dict[str, float]] = {}
    for name, adapter in adapters.items():
        alerts = runner.replay_stream(events, adapter)
        joined = join_alerts_with_labels(alerts, labels)
        results[name] = calculate_metrics(joined)

    # Assert Straph achieves 100% recall on planted Attacks 1-6
    straph_metrics = results["Straph"]
    if straph_metrics["recall"] < 1.0:
        raise AssertionError(
            f"Straph recall on planted Attacks 1-6 must be 1.0, got {straph_metrics['recall']}"
        )

    if print_table:
        table = format_comparative_markdown_table(results)
        print(table)

    return results


def main() -> int:
    """CLI entrypoint for prototype comparative evaluation."""
    run_prototype_evaluation(print_table=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
