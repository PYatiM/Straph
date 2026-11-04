"""In-memory instance record store with owner binding and idle expiration."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


@dataclass
class InstanceRecord:
    """Tracks state and history for one business object instance."""

    resource_type: str
    resource_id: str
    current_state: str
    history: list[dict[str, Any]] = field(default_factory=list)
    owner_subject: str = "anonymous"
    tainted: bool = False
    last_seen_ts: datetime = field(default_factory=lambda: datetime.now(UTC))
    idempotency_keys: set[str] = field(default_factory=set)

    @property
    def key(self) -> str:
        """Composite identifier resource_type:resource_id."""
        return f"{self.resource_type}:{self.resource_id}"


class InMemoryInstanceStore:
    """Thread-safe in-memory store for instance records."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._records: dict[str, InstanceRecord] = {}
        self._owners: dict[str, str] = {}

    def get_or_create(
        self,
        resource_type: str,
        resource_id: str,
        initial_state: str = "START",
        owner_subject: str = "anonymous",
    ) -> InstanceRecord:
        """Get existing instance record or create a new one with initial state and owner."""
        key = f"{resource_type}:{resource_id}"
        with self._lock:
            if key in self._records:
                rec = self._records[key]
                # If owner was anonymous and a concrete owner arrives, bind owner
                if rec.owner_subject == "anonymous" and owner_subject != "anonymous":
                    rec.owner_subject = owner_subject
                    self._owners[key] = owner_subject
                elif key in self._owners:
                    rec.owner_subject = self._owners[key]
                return rec

            rec = InstanceRecord(
                resource_type=resource_type,
                resource_id=resource_id,
                current_state=initial_state,
                history=[],
                owner_subject=owner_subject,
                tainted=False,
                last_seen_ts=datetime.now(UTC),
                idempotency_keys=set(),
            )
            self._records[key] = rec
            if owner_subject != "anonymous":
                self._owners[key] = owner_subject
            return rec

    def get(self, resource_type: str, resource_id: str) -> InstanceRecord | None:
        """Retrieve instance record if present."""
        key = f"{resource_type}:{resource_id}"
        with self._lock:
            return self._records.get(key)

    def update(self, record: InstanceRecord) -> None:
        """Persist or update an instance record while preserving owner immutability."""
        key = f"{record.resource_type}:{record.resource_id}"
        with self._lock:
            bound_owner = self._owners.get(key)
            if bound_owner is not None and bound_owner != "anonymous":
                record.owner_subject = bound_owner
            elif record.owner_subject != "anonymous":
                self._owners[key] = record.owner_subject

            self._records[key] = record

    def expire_idle(self, idle_timeout_sec: int) -> int:
        """Purge instances where datetime.now(timezone.utc) - last_seen_ts > idle_timeout_sec."""
        with self._lock:
            now = datetime.now(UTC)
            expired_keys: list[str] = []
            for k, rec in self._records.items():
                rec_ts = rec.last_seen_ts
                if rec_ts.tzinfo is None:
                    rec_ts = rec_ts.replace(tzinfo=UTC)
                if (now - rec_ts).total_seconds() > idle_timeout_sec:
                    expired_keys.append(k)

            for k in expired_keys:
                del self._records[k]
                self._owners.pop(k, None)

            return len(expired_keys)

    def count(self) -> int:
        """Return total active instances in memory."""
        with self._lock:
            return len(self._records)
