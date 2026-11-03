"""Non-buffering HTTP response streaming tap for asynchronous resource ID extraction."""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
from collections.abc import AsyncIterator, Callable, Sequence
from typing import Any, Self

log = logging.getLogger("straph.gateway.streaming")

DEFAULT_MAX_BUFFER_BYTES = 65536  # 64 KB


def extract_id_from_json(raw_bytes: bytes, id_fields: Sequence[str]) -> str | None:
    """Extract configured resource ID field from JSON bytes without raising."""
    try:
        doc = json.loads(raw_bytes.decode("utf-8"))
        if not isinstance(doc, dict):
            return None
        for field in id_fields:
            val = doc.get(field)
            if isinstance(val, bool):
                continue
            if isinstance(val, (int, str)):
                s = str(val).strip()
                if s:
                    return s
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        return None
    return None


class StreamTap(AsyncIterator[bytes]):
    """Async iterator wrapper tapping streamed response chunks for JSON resource ID extraction.

    Yields chunks immediately to downstream clients without delay or byte truncation.
    Buffers up to max_buffer_bytes (default 64KB) to parse the final entity ID upon
    stream completion. If the payload exceeds the limit, buffering is terminated to
    prevent unbounded memory growth while streaming continues unaffected.
    """

    def __init__(
        self,
        source: AsyncIterator[bytes | str],
        id_fields: Sequence[str] = ("order_id", "id"),
        max_buffer_bytes: int = DEFAULT_MAX_BUFFER_BYTES,
        on_complete: Callable[[str | None], Any] | None = None,
    ) -> None:
        self.source = source
        self.id_fields = tuple(id_fields)
        self.max_buffer_bytes = max_buffer_bytes
        self.on_complete = on_complete

        self.extracted_resource_id: str | None = None
        self._buffer: list[bytes] = []
        self._buffered_bytes: int = 0
        self._overflowed: bool = False
        self._completed: bool = False

    def __aiter__(self) -> Self:
        return self

    async def __anext__(self) -> bytes:
        try:
            chunk = await self.source.__anext__()
        except StopAsyncIteration:
            self._finalize()
            raise

        chunk_bytes = chunk if isinstance(chunk, bytes) else chunk.encode("utf-8")

        # Buffer incoming bytes until limit is exceeded
        if not self._overflowed:
            if self._buffered_bytes + len(chunk_bytes) <= self.max_buffer_bytes:
                self._buffer.append(chunk_bytes)
                self._buffered_bytes += len(chunk_bytes)
            else:
                self._overflowed = True
                self._buffer.clear()

        return chunk_bytes

    def _finalize(self) -> None:
        """Triggered upon normal stream completion."""
        if self._completed:
            return
        self._completed = True

        if not self._overflowed and self._buffer:
            raw = b"".join(self._buffer)
            self.extracted_resource_id = extract_id_from_json(raw, self.id_fields)

        if self.on_complete is not None:
            try:
                res = self.on_complete(self.extracted_resource_id)
                if inspect.isawaitable(res):
                    asyncio.create_task(res)
            except Exception:
                log.exception("Error in StreamTap on_complete callback")
