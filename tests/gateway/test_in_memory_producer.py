"""Tests for services/gateway/producer.py InMemoryEventProducer."""

from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path
from typing import Any

from services.gateway.models import APIEventPayload
from services.gateway.producer import EventProducerProtocol, InMemoryEventProducer, event_key

VALID: dict[str, Any] = json.loads(
    (Path(__file__).resolve().parents[2] / "contracts" / "examples" / "event_valid.json").read_text(
        encoding="utf-8"
    )
)


def make_event(**changes: Any) -> APIEventPayload:
    return APIEventPayload.model_validate({**VALID, **changes})


def test_adheres_to_protocol() -> None:
    assert isinstance(InMemoryEventProducer(), EventProducerProtocol)


def test_event_key_format() -> None:
    event = make_event(resource_type="Order", resource_id="1042")
    assert event_key(event) == "Order:1042"
    assert event_key(event) == f"{event.resource_type}:{event.resource_id}"


def test_produce_stores_key_and_event_in_order() -> None:
    producer = InMemoryEventProducer()
    a = make_event(resource_id="1")
    b = make_event(resource_type="Invoice", resource_id="2")

    async def run() -> list[bool]:
        return [
            await producer.produce_event(event_key(a), a),
            await producer.produce_event(event_key(b), b),
        ]

    assert asyncio.run(run()) == [True, True]
    assert producer.events == [("Order:1", a), ("Invoice:2", b)]


def test_events_snapshot_is_a_copy() -> None:
    producer = InMemoryEventProducer()
    asyncio.run(producer.produce_event("k", make_event()))
    producer.events.clear()
    assert len(producer.events) == 1


def test_maxlen_evicts_oldest() -> None:
    producer = InMemoryEventProducer(maxlen=2)

    async def run() -> None:
        for i in range(3):
            await producer.produce_event(f"k{i}", make_event())

    asyncio.run(run())
    assert [k for k, _ in producer.events] == ["k1", "k2"]


def test_close_rejects_further_events() -> None:
    producer = InMemoryEventProducer()

    async def run() -> bool:
        await producer.close()
        return await producer.produce_event("k", make_event())

    assert asyncio.run(run()) is False
    assert producer.events == []


def test_thread_safe_appending() -> None:
    producer = InMemoryEventProducer()
    threads, per_thread = 8, 200
    start = threading.Barrier(threads)

    def worker(n: int) -> None:
        event = make_event()
        start.wait()
        for i in range(per_thread):
            assert asyncio.run(producer.produce_event(f"{n}:{i}", event))

    pool = [threading.Thread(target=worker, args=(n,)) for n in range(threads)]
    for t in pool:
        t.start()
    for t in pool:
        t.join()

    keys = [k for k, _ in producer.events]
    assert len(keys) == threads * per_thread
    assert len(set(keys)) == len(keys)
    for n in range(threads):
        own = [k for k in keys if k.startswith(f"{n}:")]
        assert own == [f"{n}:{i}" for i in range(per_thread)]
