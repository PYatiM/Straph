"""Tests for EventDeduplicator and idempotency retry filtering."""

from __future__ import annotations

import concurrent.futures
import json
from pathlib import Path
from typing import Any

import pytest

from services.detector.classifier import classify
from services.detector.dedup import EventDeduplicator
from services.detector.store import InstanceRecord
from services.detector.tables import ModelTables, load_tables
from tests.detector.fixtures.tc8_legal_retry import get_tc8_events
from tests.detector.fixtures.tc10_duplicate_delivery import get_tc10_events

ROOT = Path(__file__).resolve().parents[2]
CHECKOUT_MODEL_PATH = ROOT / "contracts" / "examples" / "model_checkout.json"


@pytest.fixture
def tables() -> ModelTables:
    with open(CHECKOUT_MODEL_PATH, "r", encoding="utf-8") as f:
        model_dict: dict[str, Any] = json.load(f)
    return load_tables(model_dict)


def test_event_deduplicator_lru_and_thread_safety() -> None:
    """Verify EventDeduplicator detects duplicates and evicts oldest entries past max_size."""
    dedup = EventDeduplicator(max_size=3)

    assert dedup.is_duplicate("evt-1") is False
    assert dedup.is_duplicate("evt-2") is False
    assert dedup.is_duplicate("evt-3") is False

    # Immediate duplicate
    assert dedup.is_duplicate("evt-2") is True
    assert dedup.is_duplicate("evt-1") is True

    # Eviction past max_size=3: cache currently has evt-3, evt-2, evt-1
    # Adding evt-4 will evict the LRU (evt-3)
    assert dedup.is_duplicate("evt-4") is False
    assert dedup.count() == 3
    assert "evt-3" not in dedup
    assert "evt-1" in dedup


def test_concurrent_deduplication() -> None:
    """Verify thread-safety of EventDeduplicator under concurrent access."""
    dedup = EventDeduplicator(max_size=5000)

    def worker(worker_id: int) -> list[bool]:
        results: list[bool] = []
        for i in range(100):
            # Each worker delivers event IDs 0..49 twice
            evt_id = f"event-concurrent-{i % 50}"
            results.append(dedup.is_duplicate(evt_id))
        return results

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(worker, w) for w in range(8)]
        all_results: list[bool] = []
        for f in concurrent.futures.as_completed(futures):
            all_results.extend(f.result())

    # Exactly 50 unique IDs should have returned False (first time seen)
    first_seen_count = all_results.count(False)
    assert first_seen_count == 50
    assert dedup.count() == 50


def test_tc8_legal_retry_generates_zero_alerts(tables: ModelTables) -> None:
    """Verify TC8 failed payment followed by legal retry with same idempotency key raises NO alerts."""
    order = InstanceRecord(
        resource_type="Order",
        resource_id="1008",
        current_state="START",
        owner_subject="customer-1008",
    )
    cart = InstanceRecord(
        resource_type="Cart",
        resource_id="cart-1008",
        current_state="START",
        owner_subject="customer-1008",
    )

    events = get_tc8_events()
    for event in events:
        inst = order if event.get("resource_type") == "Order" else cart
        alert = classify(inst, event, tables)
        assert alert is None

    # After event 4 (500), state was ADDRESS_SET. After event 5 (200 retry), state is PAYMENT_PENDING
    assert order.current_state == "PAYMENT_PENDING"
    assert "idemp-999" in order.idempotency_keys or any("idemp-999" in k for k in order.idempotency_keys)


def test_tc10_duplicate_kafka_delivery_generates_zero_alerts(tables: ModelTables) -> None:
    """Verify TC10 duplicate Kafka delivery is filtered by EventDeduplicator with zero alerts."""
    order = InstanceRecord(
        resource_type="Order",
        resource_id="1010",
        current_state="START",
        owner_subject="customer-1010",
    )
    cart = InstanceRecord(
        resource_type="Cart",
        resource_id="cart-1010",
        current_state="START",
        owner_subject="customer-1010",
    )

    dedup = EventDeduplicator(max_size=1000)
    events = get_tc10_events()

    alerts: list[Any] = []
    processed_count = 0

    for event in events:
        evt_id = event["event_id"]
        if dedup.is_duplicate(evt_id):
            # Duplicate Kafka delivery filtered out
            continue

        processed_count += 1
        inst = order if event.get("resource_type") == "Order" else cart
        alert = classify(inst, event, tables)
        if alert is not None:
            alerts.append(alert)

    # 4 events in TC10, the 4th is a verbatim duplicate of the 3rd
    assert processed_count == 3
    assert alerts == []
    assert order.current_state == "ADDRESS_SET"
