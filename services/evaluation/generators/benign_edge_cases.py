"""Benign Edge-Case Traffic Generator (retries, timeouts, multi-edits).

Generates schema-valid API events and GroundTruthLabel records representing
valid business variations that must not trigger false-positive detections:
1. Multiple address edits before payment (ADDRESS_SET -> ADDRESS_SET self-loop).
2. Client payment retry after a 500 error sharing the same idempotency_key.
3. Long delay (2 hours) between address setting and payment initiation.
"""

from __future__ import annotations

import random
import uuid
from typing import Any

from services.evaluation.labels import ExpectedLabel, GroundTruthLabel

SCENARIO_MULTI_EDIT = "BENIGN_EDGE_MULTI_ADDRESS_EDIT"
SCENARIO_PAYMENT_RETRY = "BENIGN_EDGE_IDEMPOTENT_PAYMENT_RETRY"
SCENARIO_LONG_DELAY = "BENIGN_EDGE_LONG_DELAY_PAYMENT"


def _seeded_uuid4(rng: random.Random) -> str:
    """Generate a deterministic RFC-4122 UUID4 string from a seeded RNG."""
    return str(uuid.UUID(int=rng.getrandbits(128), version=4))


def _make_event(
    rng: random.Random,
    ts_received: str,
    ts_completed: str,
    sub: str,
    role: str,
    method: str,
    path_template: str,
    resource_type: str,
    resource_id: str,
    status_code: int,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Build a schema-compliant API event dictionary."""
    event: dict[str, Any] = {
        "event_id": _seeded_uuid4(rng),
        "ts_received": ts_received,
        "ts_completed": ts_completed,
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
        "resource_type": resource_type,
        "resource_id": resource_id,
        "status_code": status_code,
    }
    if idempotency_key is not None:
        event["idempotency_key"] = idempotency_key
    return event


def _make_benign_label(event_id: str, scenario_id: str, seed: int) -> GroundTruthLabel:
    """Build a GroundTruthLabel marked as benign (is_violation=False)."""
    return GroundTruthLabel(
        event_id=event_id,
        is_violation=False,
        expected_label=ExpectedLabel.BENIGN,
        expected_rule=None,
        missing_ops=[],
        scenario_id=scenario_id,
        seed=seed,
    )


def generate_benign_edge_cases(
    seed: int = 42,
) -> tuple[list[dict[str, Any]], list[GroundTruthLabel]]:
    """Generate benign edge-case event sequences and ground-truth labels.

    Sequences:
    1. Multiple address edits before payment (`ADDRESS_SET -> ADDRESS_SET`) on order `edge-1009`.
    2. Client payment retry after 500 error sharing same `idempotency_key` on order `edge-1008`.
    3. Long delay (2 hours) between address setting and payment on order `edge-1010`.

    Args:
        seed: Deterministic seed for event IDs.

    Returns:
        Tuple of (events, labels) where all labels have is_violation=False
        and expected_label="BENIGN".
    """
    rng = random.Random(f"benign_edge_cases:{seed}")
    events: list[dict[str, Any]] = []
    labels: list[GroundTruthLabel] = []

    # -------------------------------------------------------------------------
    # Sequence 1: Multiple address edits before payment (ADDRESS_SET -> ADDRESS_SET)
    # -------------------------------------------------------------------------
    order_1 = "edge-1009"
    sub_1 = f"customer-{order_1}"
    seq_1_specs: list[tuple[str, str, str, str, int, str, str, str | None]] = [
        (
            "2026-10-29T17:00:00.000Z",
            "2026-10-29T17:00:00.050Z",
            sub_1,
            "customer",
            "POST",
            "/orders",
            201,
            None,
        ),
        (
            "2026-10-29T17:01:00.000Z",
            "2026-10-29T17:01:00.050Z",
            sub_1,
            "customer",
            "PUT",
            "/orders/{id}/address",
            200,
            None,
        ),
        (
            "2026-10-29T17:02:00.000Z",
            "2026-10-29T17:02:00.050Z",
            sub_1,
            "customer",
            "PUT",
            "/orders/{id}/address",
            200,
            None,
        ),
        (
            "2026-10-29T17:03:00.000Z",
            "2026-10-29T17:03:00.050Z",
            sub_1,
            "customer",
            "POST",
            "/orders/{id}/payment",
            200,
            None,
        ),
    ]
    for ts_r, ts_c, sub, role, method, path, status, idem in seq_1_specs:
        ev = _make_event(rng, ts_r, ts_c, sub, role, method, path, "Order", order_1, status, idem)
        events.append(ev)
        labels.append(_make_benign_label(ev["event_id"], SCENARIO_MULTI_EDIT, seed))

    # -------------------------------------------------------------------------
    # Sequence 2: Client payment retry after 500 error sharing same idempotency_key
    # -------------------------------------------------------------------------
    order_2 = "edge-1008"
    sub_2 = f"customer-{order_2}"
    idem_key = f"idemp-retry-{seed}"
    seq_2_specs: list[tuple[str, str, str, str, int, str, str, str | None]] = [
        (
            "2026-10-29T17:10:00.000Z",
            "2026-10-29T17:10:00.050Z",
            sub_2,
            "customer",
            "POST",
            "/orders",
            201,
            None,
        ),
        (
            "2026-10-29T17:11:00.000Z",
            "2026-10-29T17:11:00.050Z",
            sub_2,
            "customer",
            "PUT",
            "/orders/{id}/address",
            200,
            None,
        ),
        (
            "2026-10-29T17:12:00.000Z",
            "2026-10-29T17:12:00.050Z",
            sub_2,
            "customer",
            "POST",
            "/orders/{id}/payment",
            500,
            idem_key,
        ),
        (
            "2026-10-29T17:13:00.000Z",
            "2026-10-29T17:13:00.050Z",
            sub_2,
            "customer",
            "POST",
            "/orders/{id}/payment",
            200,
            idem_key,
        ),
    ]
    for ts_r, ts_c, sub, role, method, path, status, idem in seq_2_specs:
        ev = _make_event(rng, ts_r, ts_c, sub, role, method, path, "Order", order_2, status, idem)
        events.append(ev)
        labels.append(_make_benign_label(ev["event_id"], SCENARIO_PAYMENT_RETRY, seed))

    # -------------------------------------------------------------------------
    # Sequence 3: Long delay (2 hours) between address setting and payment
    # -------------------------------------------------------------------------
    order_3 = "edge-1010"
    sub_3 = f"customer-{order_3}"
    seq_3_specs: list[tuple[str, str, str, str, int, str, str, str | None]] = [
        (
            "2026-10-29T18:00:00.000Z",
            "2026-10-29T18:00:00.050Z",
            sub_3,
            "customer",
            "POST",
            "/orders",
            201,
            None,
        ),
        (
            "2026-10-29T18:01:00.000Z",
            "2026-10-29T18:01:00.050Z",
            sub_3,
            "customer",
            "PUT",
            "/orders/{id}/address",
            200,
            None,
        ),
        (
            "2026-10-29T20:01:00.000Z",
            "2026-10-29T20:01:00.050Z",
            sub_3,
            "customer",
            "POST",
            "/orders/{id}/payment",
            200,
            None,
        ),
    ]
    for ts_r, ts_c, sub, role, method, path, status, idem in seq_3_specs:
        ev = _make_event(rng, ts_r, ts_c, sub, role, method, path, "Order", order_3, status, idem)
        events.append(ev)
        labels.append(_make_benign_label(ev["event_id"], SCENARIO_LONG_DELAY, seed))

    return events, labels
