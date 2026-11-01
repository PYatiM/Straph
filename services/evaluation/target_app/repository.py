"""In-memory, thread-safe order store for the Straph evaluation target app.

The repository owns the order lifecycle (the "lifecycle guards"). In strict mode every
state change must follow ``ALLOWED_TRANSITIONS``. Strict mode can be switched off so the
target app can deliberately behave like a vulnerable system when attacks are replayed.

The repository records *who* made each change (``actor_sub``) but does not decide whether
that caller is allowed to. Ownership checks belong to the API layer, which is the part
Straph's R0 (IDENTITY_MISMATCH) rule observes from outside.
"""

from __future__ import annotations

import itertools
import threading
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType


class OrderState(StrEnum):
    """Lifecycle states of an order."""

    ORDER_CREATED = "ORDER_CREATED"
    ADDRESS_SET = "ADDRESS_SET"
    PAYMENT_PENDING = "PAYMENT_PENDING"
    PAID = "PAID"
    FULFILLMENT_QUEUED = "FULFILLMENT_QUEUED"
    SHIPPED = "SHIPPED"
    DELIVERED = "DELIVERED"
    CANCELLED = "CANCELLED"
    REFUNDED = "REFUNDED"


ALLOWED_TRANSITIONS: Mapping[OrderState, frozenset[OrderState]] = MappingProxyType(
    {
        OrderState.ORDER_CREATED: frozenset({OrderState.ADDRESS_SET, OrderState.CANCELLED}),
        # ADDRESS_SET -> ADDRESS_SET is legal: the customer may correct the address.
        OrderState.ADDRESS_SET: frozenset(
            {OrderState.ADDRESS_SET, OrderState.PAYMENT_PENDING, OrderState.CANCELLED}
        ),
        OrderState.PAYMENT_PENDING: frozenset({OrderState.PAID, OrderState.CANCELLED}),
        OrderState.PAID: frozenset(
            {OrderState.FULFILLMENT_QUEUED, OrderState.CANCELLED, OrderState.REFUNDED}
        ),
        OrderState.FULFILLMENT_QUEUED: frozenset({OrderState.SHIPPED}),
        OrderState.SHIPPED: frozenset({OrderState.DELIVERED}),
        OrderState.DELIVERED: frozenset({OrderState.REFUNDED}),
        OrderState.CANCELLED: frozenset(),
        OrderState.REFUNDED: frozenset(),
    }
)

INITIAL_STATE = OrderState.ORDER_CREATED


class OrderRepositoryError(Exception):
    """Base class for repository errors."""


class OrderNotFoundException(OrderRepositoryError):
    """Raised when an order id is unknown."""

    def __init__(self, order_id: str) -> None:
        super().__init__(f"order not found: {order_id}")
        self.order_id = order_id


class InvalidStateTransitionException(OrderRepositoryError):
    """Raised in strict mode when a transition is not allowed from the current state."""

    def __init__(self, order_id: str, current: OrderState, requested: OrderState) -> None:
        super().__init__(
            f"illegal transition for order {order_id}: {current.value} -> {requested.value}"
        )
        self.order_id = order_id
        self.current = current
        self.requested = requested


@dataclass(frozen=True, slots=True)
class OrderItem:
    """A single line item."""

    sku: str
    quantity: int

    def __post_init__(self) -> None:
        if not self.sku:
            raise ValueError("sku must not be empty")
        if self.quantity < 1:
            raise ValueError("quantity must be at least 1")


@dataclass(frozen=True, slots=True)
class HistoryEntry:
    """One recorded state change."""

    from_state: OrderState | None
    to_state: OrderState
    actor_sub: str
    at: datetime


@dataclass(frozen=True, slots=True)
class OrderRecord:
    """Immutable snapshot of an order. Updates create a new snapshot."""

    order_id: str
    state: OrderState
    owner_sub: str
    items: tuple[OrderItem, ...]
    address: str | None = None
    payment_id: str | None = None
    refunded: bool = False
    history: tuple[HistoryEntry, ...] = field(default_factory=tuple)


FIRST_ORDER_ID = 1001


def _utc_now() -> datetime:
    return datetime.now(UTC)


class OrderRepository:
    """Thread-safe in-memory store of orders.

    Args:
        strict: When True (default), illegal transitions raise
            ``InvalidStateTransitionException``. When False, any transition is applied.
        id_factory: Produces new order ids (injectable for tests). By default ids are
            numeric strings counting up from 1001.
        clock: Produces timestamps for history entries (injectable for tests).
    """

    def __init__(
        self,
        *,
        strict: bool = True,
        id_factory: Callable[[], str] | None = None,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._strict = strict
        counter = itertools.count(FIRST_ORDER_ID)
        self._id_factory: Callable[[], str] = id_factory or (lambda: str(next(counter)))
        self._clock = clock
        self._orders: dict[str, OrderRecord] = {}
        self._lock = threading.RLock()

    @property
    def strict(self) -> bool:
        return self._strict

    def create_order(self, owner_sub: str, items: Iterable[OrderItem]) -> OrderRecord:
        """Create an order in ``CREATED`` state and return its snapshot."""
        if not owner_sub:
            raise ValueError("owner_sub must not be empty")
        item_tuple = tuple(items)
        if not item_tuple:
            raise ValueError("an order needs at least one item")
        with self._lock:
            order_id = self._id_factory()
            if order_id in self._orders:
                raise OrderRepositoryError(f"duplicate order id: {order_id}")
            record = OrderRecord(
                order_id=order_id,
                state=INITIAL_STATE,
                owner_sub=owner_sub,
                items=item_tuple,
                history=(HistoryEntry(None, INITIAL_STATE, owner_sub, self._clock()),),
            )
            self._orders[order_id] = record
            return record

    def get_order(self, order_id: str) -> OrderRecord:
        """Return the current snapshot or raise ``OrderNotFoundException``."""
        with self._lock:
            try:
                return self._orders[order_id]
            except KeyError:
                raise OrderNotFoundException(order_id) from None

    def update_order_state(
        self,
        order_id: str,
        new_state: OrderState,
        actor_sub: str,
        *,
        address: str | None = None,
        payment_id: str | None = None,
    ) -> OrderRecord:
        """Move an order to ``new_state`` and return the new snapshot.

        The check and the write happen under one lock, so two threads cannot both pass
        the guard for the same state. ``address`` and ``payment_id`` are optional data
        attached to the transition. Entering ``REFUNDED`` sets ``refunded=True``.
        """
        if not actor_sub:
            raise ValueError("actor_sub must not be empty")
        with self._lock:
            current = self.get_order(order_id)
            if self._strict and new_state not in ALLOWED_TRANSITIONS[current.state]:
                raise InvalidStateTransitionException(order_id, current.state, new_state)
            entry = HistoryEntry(current.state, new_state, actor_sub, self._clock())
            updated = replace(
                current,
                state=new_state,
                address=address if address is not None else current.address,
                payment_id=payment_id if payment_id is not None else current.payment_id,
                refunded=current.refunded or new_state is OrderState.REFUNDED,
                history=(*current.history, entry),
            )
            self._orders[order_id] = updated
            return updated


@dataclass(frozen=True, slots=True)
class CartSummary:
    """Snapshot of one caller's cart."""

    owner_sub: str
    items: tuple[OrderItem, ...]

    @property
    def total_quantity(self) -> int:
        return sum(item.quantity for item in self.items)


class CartStore:
    """Thread-safe in-memory carts, one per caller ``sub``."""

    def __init__(self) -> None:
        self._carts: dict[str, tuple[OrderItem, ...]] = {}
        self._lock = threading.Lock()

    def add_item(self, owner_sub: str, item: OrderItem) -> CartSummary:
        """Add an item (merging quantities for the same SKU) and return the cart."""
        with self._lock:
            current = self._carts.get(owner_sub, ())
            merged: list[OrderItem] = []
            found = False
            for existing in current:
                if existing.sku == item.sku:
                    merged.append(OrderItem(item.sku, existing.quantity + item.quantity))
                    found = True
                else:
                    merged.append(existing)
            if not found:
                merged.append(item)
            self._carts[owner_sub] = tuple(merged)
            return CartSummary(owner_sub, self._carts[owner_sub])

    def get_cart(self, owner_sub: str) -> CartSummary:
        with self._lock:
            return CartSummary(owner_sub, self._carts.get(owner_sub, ()))

    def clear(self, owner_sub: str) -> None:
        with self._lock:
            self._carts.pop(owner_sub, None)
