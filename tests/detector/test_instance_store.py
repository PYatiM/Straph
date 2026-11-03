"""Tests for InMemoryInstanceStore and InstanceRecord."""

from __future__ import annotations

import concurrent.futures
from datetime import UTC, datetime, timedelta

from services.detector.store import InMemoryInstanceStore


def test_instance_creation() -> None:
    """Verify instance creation with default and specified parameters."""
    store = InMemoryInstanceStore()
    rec = store.get_or_create("Order", "101", initial_state="CART_ACTIVE", owner_subject="user-1")

    assert rec.resource_type == "Order"
    assert rec.resource_id == "101"
    assert rec.current_state == "CART_ACTIVE"
    assert rec.owner_subject == "user-1"
    assert rec.tainted is False
    assert rec.history == []
    assert rec.idempotency_keys == set()
    assert rec.last_seen_ts.tzinfo is not None

    # Re-getting the same instance returns the identical object
    rec_again = store.get_or_create("Order", "101")
    assert rec_again is rec


def test_state_mutation() -> None:
    """Verify instance state mutation and persistence in the store."""
    store = InMemoryInstanceStore()
    rec = store.get_or_create("Order", "102", initial_state="ORDER_CREATED", owner_subject="user-2")

    rec.current_state = "ADDRESS_SET"
    rec.history.append({"operation": "PUT /orders/{id}/address", "status": 200})
    rec.tainted = True
    store.update(rec)

    fetched = store.get("Order", "102")
    assert fetched is not None
    assert fetched.current_state == "ADDRESS_SET"
    assert len(fetched.history) == 1
    assert fetched.tainted is True


def test_owner_immutability() -> None:
    """Verify owner cannot be overwritten once bound."""
    store = InMemoryInstanceStore()
    rec = store.get_or_create("Order", "103", initial_state="START", owner_subject="alice")
    assert rec.owner_subject == "alice"

    # Attempting to re-create or get with another owner should not change it
    rec_fetched = store.get_or_create("Order", "103", owner_subject="bob")
    assert rec_fetched.owner_subject == "alice"

    # Attempting to mutate owner via update should preserve original owner
    rec_fetched.owner_subject = "attacker"
    store.update(rec_fetched)

    rec_final = store.get("Order", "103")
    assert rec_final is not None
    assert rec_final.owner_subject == "alice"


def test_anonymous_owner_binding() -> None:
    """Verify anonymous instance can later be bound to an authenticated owner."""
    store = InMemoryInstanceStore()
    rec = store.get_or_create("Order", "104", initial_state="START", owner_subject="anonymous")
    assert rec.owner_subject == "anonymous"

    # Subsequent call with a known identity should bind the owner
    rec_bound = store.get_or_create("Order", "104", owner_subject="charlie")
    assert rec_bound.owner_subject == "charlie"


def test_expire_idle() -> None:
    """Verify idle records past the timeout are purged."""
    store = InMemoryInstanceStore()
    now = datetime.now(UTC)

    rec_active = store.get_or_create("Order", "active_1", initial_state="START")
    rec_active.last_seen_ts = now

    rec_idle = store.get_or_create("Order", "idle_1", initial_state="START")
    rec_idle.last_seen_ts = now - timedelta(seconds=120)

    rec_ancient = store.get_or_create("Order", "idle_2", initial_state="START")
    rec_ancient.last_seen_ts = now - timedelta(seconds=500)

    assert store.count() == 3

    # Purge records idle for more than 60 seconds
    purged = store.expire_idle(idle_timeout_sec=60)
    assert purged == 2
    assert store.count() == 1
    assert store.get("Order", "active_1") is not None
    assert store.get("Order", "idle_1") is None
    assert store.get("Order", "idle_2") is None


def test_concurrent_access() -> None:
    """Verify thread-safety under concurrent access."""
    store = InMemoryInstanceStore()

    def worker(worker_id: int) -> None:
        for i in range(50):
            res_id = f"item_{i % 5}"
            rec = store.get_or_create("Item", res_id, initial_state="NEW", owner_subject=f"worker_{worker_id}")
            rec.current_state = f"STATE_{worker_id}_{i}"
            store.update(rec)

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(worker, w) for w in range(8)]
        for f in concurrent.futures.as_completed(futures):
            f.result()

    assert store.count() == 5
