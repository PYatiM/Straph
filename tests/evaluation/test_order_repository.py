"""Unit tests for the target app OrderRepository."""

from __future__ import annotations

import threading
from datetime import UTC, datetime

import pytest

from services.evaluation.target_app.repository import (
    ALLOWED_TRANSITIONS,
    InvalidStateTransitionException,
    OrderItem,
    OrderNotFoundException,
    OrderRepository,
    OrderState,
)

ITEMS = [OrderItem(sku="SKU-1", quantity=2)]
FIXED_TIME = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.fixture
def repo() -> OrderRepository:
    return OrderRepository(clock=lambda: FIXED_TIME)


def _advance(repo: OrderRepository, order_id: str, *states: OrderState) -> None:
    for state in states:
        repo.update_order_state(order_id, state, "alice")


# --- creation ---------------------------------------------------------------------------


def test_create_order_starts_in_created_state(repo: OrderRepository) -> None:
    order = repo.create_order("alice", ITEMS)
    assert order.state is OrderState.ORDER_CREATED
    assert order.owner_sub == "alice"
    assert order.items == tuple(ITEMS)
    assert order.address is None
    assert order.payment_id is None
    assert order.refunded is False


def test_create_order_records_initial_history(repo: OrderRepository) -> None:
    order = repo.create_order("alice", ITEMS)
    assert len(order.history) == 1
    assert order.history[0].from_state is None
    assert order.history[0].to_state is OrderState.ORDER_CREATED
    assert order.history[0].actor_sub == "alice"


def test_create_order_generates_unique_ids(repo: OrderRepository) -> None:
    ids = {repo.create_order("alice", ITEMS).order_id for _ in range(50)}
    assert len(ids) == 50


def test_create_order_rejects_empty_owner(repo: OrderRepository) -> None:
    with pytest.raises(ValueError):
        repo.create_order("", ITEMS)


def test_create_order_rejects_empty_items(repo: OrderRepository) -> None:
    with pytest.raises(ValueError):
        repo.create_order("alice", [])


def test_order_item_rejects_bad_quantity() -> None:
    with pytest.raises(ValueError):
        OrderItem(sku="SKU-1", quantity=0)


# --- retrieval --------------------------------------------------------------------------


def test_get_order_returns_stored_state(repo: OrderRepository) -> None:
    order = repo.create_order("alice", ITEMS)
    assert repo.get_order(order.order_id) == order


def test_get_order_unknown_id_raises(repo: OrderRepository) -> None:
    with pytest.raises(OrderNotFoundException):
        repo.get_order("missing")


def test_update_unknown_order_raises(repo: OrderRepository) -> None:
    with pytest.raises(OrderNotFoundException):
        repo.update_order_state("missing", OrderState.ORDER_ADDRESS_SET, "alice")


def test_snapshots_are_immutable(repo: OrderRepository) -> None:
    order = repo.create_order("alice", ITEMS)
    with pytest.raises(AttributeError):
        order.state = OrderState.ORDER_PAID  # type: ignore[misc]


# --- legal transitions ------------------------------------------------------------------


def test_full_happy_path(repo: OrderRepository) -> None:
    order = repo.create_order("alice", ITEMS)
    _advance(
        repo,
        order.order_id,
        OrderState.ORDER_ADDRESS_SET,
        OrderState.ORDER_PAID,
        OrderState.ORDER_SHIPPED,
        OrderState.ORDER_DELIVERED,
    )
    final = repo.get_order(order.order_id)
    assert final.state is OrderState.ORDER_DELIVERED
    assert [h.to_state for h in final.history] == [
        OrderState.ORDER_CREATED,
        OrderState.ORDER_ADDRESS_SET,
        OrderState.ORDER_PAID,
        OrderState.ORDER_SHIPPED,
        OrderState.ORDER_DELIVERED,
    ]


def test_update_records_actor_and_previous_state(repo: OrderRepository) -> None:
    order = repo.create_order("alice", ITEMS)
    repo.update_order_state(order.order_id, OrderState.ORDER_ADDRESS_SET, "bob")
    entry = repo.get_order(order.order_id).history[-1]
    assert entry.actor_sub == "bob"
    assert entry.from_state is OrderState.ORDER_CREATED


def test_update_attaches_address_and_payment(repo: OrderRepository) -> None:
    order = repo.create_order("alice", ITEMS)
    repo.update_order_state(
        order.order_id, OrderState.ORDER_ADDRESS_SET, "alice", address="1 Main St"
    )
    repo.update_order_state(order.order_id, OrderState.ORDER_PAID, "alice", payment_id="pay_1")
    final = repo.get_order(order.order_id)
    assert final.address == "1 Main St"
    assert final.payment_id == "pay_1"


def test_refund_sets_refunded_flag(repo: OrderRepository) -> None:
    order = repo.create_order("alice", ITEMS)
    _advance(
        repo,
        order.order_id,
        OrderState.ORDER_ADDRESS_SET,
        OrderState.ORDER_PAID,
        OrderState.ORDER_REFUNDED,
    )
    assert repo.get_order(order.order_id).refunded is True


# --- illegal transitions (strict mode) --------------------------------------------------


def test_skipping_states_is_rejected(repo: OrderRepository) -> None:
    order = repo.create_order("alice", ITEMS)
    with pytest.raises(InvalidStateTransitionException) as exc:
        repo.update_order_state(order.order_id, OrderState.ORDER_SHIPPED, "alice")
    assert exc.value.current is OrderState.ORDER_CREATED
    assert exc.value.requested is OrderState.ORDER_SHIPPED


def test_replaying_a_step_is_rejected(repo: OrderRepository) -> None:
    order = repo.create_order("alice", ITEMS)
    _advance(repo, order.order_id, OrderState.ORDER_ADDRESS_SET, OrderState.ORDER_PAID)
    with pytest.raises(InvalidStateTransitionException):
        repo.update_order_state(order.order_id, OrderState.ORDER_PAID, "alice")


def test_going_backwards_is_rejected(repo: OrderRepository) -> None:
    order = repo.create_order("alice", ITEMS)
    _advance(repo, order.order_id, OrderState.ORDER_ADDRESS_SET, OrderState.ORDER_PAID)
    with pytest.raises(InvalidStateTransitionException):
        repo.update_order_state(order.order_id, OrderState.ORDER_ADDRESS_SET, "alice")


def test_refund_before_payment_is_rejected(repo: OrderRepository) -> None:
    order = repo.create_order("alice", ITEMS)
    with pytest.raises(InvalidStateTransitionException):
        repo.update_order_state(order.order_id, OrderState.ORDER_REFUNDED, "alice")


@pytest.mark.parametrize("terminal", [OrderState.ORDER_CANCELLED, OrderState.ORDER_REFUNDED])
def test_terminal_states_have_no_exits(terminal: OrderState) -> None:
    assert ALLOWED_TRANSITIONS[terminal] == frozenset()


def test_rejected_move_leaves_order_unchanged(repo: OrderRepository) -> None:
    order = repo.create_order("alice", ITEMS)
    with pytest.raises(InvalidStateTransitionException):
        repo.update_order_state(order.order_id, OrderState.ORDER_DELIVERED, "alice")
    after = repo.get_order(order.order_id)
    assert after.state is OrderState.ORDER_CREATED
    assert len(after.history) == 1


def test_every_state_has_a_transition_entry() -> None:
    assert set(ALLOWED_TRANSITIONS) == set(OrderState)


# --- non-strict mode --------------------------------------------------------------------


def test_non_strict_mode_allows_illegal_move() -> None:
    repo = OrderRepository(strict=False)
    order = repo.create_order("alice", ITEMS)
    updated = repo.update_order_state(order.order_id, OrderState.ORDER_SHIPPED, "mallory")
    assert updated.state is OrderState.ORDER_SHIPPED


# --- concurrency ------------------------------------------------------------------------


def test_concurrent_updates_allow_exactly_one_winner(repo: OrderRepository) -> None:
    order = repo.create_order("alice", ITEMS)
    barrier = threading.Barrier(8)
    results: list[str] = []
    lock = threading.Lock()

    def worker() -> None:
        barrier.wait()
        try:
            repo.update_order_state(order.order_id, OrderState.ORDER_ADDRESS_SET, "alice")
            outcome = "ok"
        except InvalidStateTransitionException:
            outcome = "rejected"
        with lock:
            results.append(outcome)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert results.count("ok") == 1
    assert results.count("rejected") == 7
    assert len(repo.get_order(order.order_id).history) == 2
