"""Unit tests for StreamTap streaming body wrapper and resource ID extraction."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import pytest

from services.gateway.streaming import StreamTap


async def make_async_stream(chunks: list[bytes]) -> AsyncIterator[bytes]:
    """Helper creating an async iterator from a list of bytes."""
    for chunk in chunks:
        yield chunk


@pytest.mark.anyio
async def test_streaming_delivers_identical_chunks_without_truncation() -> None:
    """Assert client receives exact identical chunks in real-time with zero truncation."""
    chunks = [
        b'{"order_id": ',
        b'"ord-98765", ',
        b'"status": "created", ',
        b'"items": [{"sku": "SKU-1", "qty": 1}]}',
    ]

    callback_result: list[str | None] = []

    def on_complete(extracted_id: str | None) -> None:
        callback_result.append(extracted_id)

    stream = make_async_stream(chunks)
    tap = StreamTap(
        source=stream,
        id_fields=("order_id", "id"),
        on_complete=on_complete,
    )

    received_chunks: list[bytes] = []
    async for chunk in tap:
        received_chunks.append(chunk)

    # 1. Exact chunk match
    assert received_chunks == chunks
    assert b"".join(received_chunks) == b"".join(chunks)

    # 2. Extracted resource ID matches and made available to callback
    assert tap.extracted_resource_id == "ord-98765"
    assert callback_result == ["ord-98765"]


@pytest.mark.anyio
async def test_streaming_fallback_id_field() -> None:
    """Verify fallback to subsequent configured ID fields."""
    chunks = [
        b'{"uuid": "ignored-uuid", ',
        b'"id": "fallback-entity-42"}',
    ]

    tap = StreamTap(
        source=make_async_stream(chunks),
        id_fields=("order_id", "id"),
    )

    _ = [c async for c in tap]
    assert tap.extracted_resource_id == "fallback-entity-42"


@pytest.mark.anyio
async def test_large_stream_exceeding_buffer_drops_buffer_safely() -> None:
    """Verify payloads larger than max_buffer_bytes stream completely without leaking memory."""
    # Create chunks totaling > 64KB
    chunk_size = 10 * 1024
    num_chunks = 10  # 100 KB total
    large_chunks = [b"x" * chunk_size for _ in range(num_chunks)]

    tap = StreamTap(
        source=make_async_stream(large_chunks),
        max_buffer_bytes=64 * 1024,
    )

    received: list[bytes] = [c async for c in tap]
    assert len(received) == num_chunks
    assert sum(len(c) for c in received) == 100 * 1024

    # Buffer should overflow and clear to save memory
    assert tap._overflowed is True
    assert tap._buffer == []
    assert tap.extracted_resource_id is None


@pytest.mark.anyio
async def test_error_propagation_through_stream() -> None:
    """Verify upstream stream errors are re-raised immediately to the client."""

    async def exploding_stream() -> AsyncIterator[bytes]:
        yield b"chunk-1"
        raise ConnectionResetError("downstream reset")

    tap = StreamTap(source=exploding_stream())

    with pytest.raises(ConnectionResetError, match="downstream reset"):
        async for _ in tap:
            pass


@pytest.mark.anyio
async def test_async_callback_execution() -> None:
    """Verify async on_complete callback is scheduled and executed."""
    async_received: list[str | None] = []

    async def async_cb(res_id: str | None) -> None:
        async_received.append(res_id)

    payload = json.dumps({"order_id": "async-123"}).encode("utf-8")
    tap = StreamTap(
        source=make_async_stream([payload]),
        on_complete=async_cb,
    )

    _ = [c async for c in tap]
    # Allow async task to complete
    assert tap.extracted_resource_id == "async-123"
