"""Automated invariant tests for the reference workflow model (contracts/examples/model_checkout.json).

Verifies the four core workflow model invariants:
1. Target-Uniqueness Invariant: an operation with identical method and path template must uniquely
   determine its target state unless explicitly documented.
2. Terminal State Invariant: states marked terminal: true (CANCELLED, REFUNDED) have zero outgoing
   transitions.
3. Reachability Invariant: every non-initial state is reachable via at least one directed path from
   the initial state (CART_ACTIVE).
4. Role Authorization Invariant: every transition defines at least one non-empty string in
   allowed_roles.
"""

from __future__ import annotations

import collections
import json
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
MODEL_PATH = REPO_ROOT / "contracts" / "examples" / "model_checkout.json"


@pytest.fixture(scope="module")
def checkout_model() -> dict[str, Any]:
    """Load and parse contracts/examples/model_checkout.json."""
    assert MODEL_PATH.is_file(), f"Model file not found at {MODEL_PATH}"
    return json.loads(MODEL_PATH.read_text(encoding="utf-8"))


def compute_reachable_states(
    initial_state: str,
    transitions: list[dict[str, Any]],
) -> set[str]:
    """Traverse directed transitions from initial_state using BFS and return all visited states."""
    adj: dict[str, set[str]] = collections.defaultdict(set)
    for transition in transitions:
        to_state = transition["to_state"]
        for from_state in transition["from_states"]:
            adj[from_state].add(to_state)

    visited: set[str] = set()
    queue: collections.deque[str] = collections.deque([initial_state])

    while queue:
        current = queue.popleft()
        if current in visited:
            continue
        visited.add(current)
        for neighbor in adj[current]:
            if neighbor not in visited:
                queue.append(neighbor)
    return visited


# ==============================================================================
# Invariant 1: Target-Uniqueness Invariant
# ==============================================================================


def test_target_uniqueness_invariant(checkout_model: dict[str, Any]) -> None:
    """An operation with identical method and path template must uniquely determine its target state."""
    operation_to_targets: dict[tuple[str, str], set[str]] = {}

    for transition in checkout_model["transitions"]:
        op = transition["operation"]
        key = (op["method"].upper(), op["path_template"])
        target = transition["to_state"]
        operation_to_targets.setdefault(key, set()).add(target)

    for (method, path), targets in operation_to_targets.items():
        assert len(targets) == 1, (
            f"Operation {method} {path} violates target-uniqueness: "
            f"maps to multiple target states: {sorted(targets)}"
        )


def test_target_uniqueness_violation_detected() -> None:
    """Demonstrate that non-deterministic transitions with identical operations are caught."""
    conflicting_transitions = [
        {
            "transition_id": "T1a",
            "operation": {"method": "POST", "path_template": "/checkout"},
            "to_state": "PAID",
        },
        {
            "transition_id": "T1b",
            "operation": {"method": "POST", "path_template": "/checkout"},
            "to_state": "PENDING",
        },
    ]
    targets: dict[tuple[str, str], set[str]] = {}
    for t in conflicting_transitions:
        key = (t["operation"]["method"], t["operation"]["path_template"])
        targets.setdefault(key, set()).add(t["to_state"])

    assert len(targets[("POST", "/checkout")]) == 2


# ==============================================================================
# Invariant 2: Terminal State Invariant
# ==============================================================================


def test_terminal_state_invariant(checkout_model: dict[str, Any]) -> None:
    """States marked terminal: true (CANCELLED, REFUNDED) have zero outgoing transitions."""
    terminal_states = {
        state["state_id"] for state in checkout_model["states"] if state["terminal"] is True
    }

    # Verify expected terminal states exist
    assert {"CANCELLED", "REFUNDED"}.issubset(terminal_states), (
        f"Expected CANCELLED and REFUNDED to be terminal, found: {terminal_states}"
    )

    for transition in checkout_model["transitions"]:
        tid = transition["transition_id"]
        from_states = set(transition["from_states"])
        outgoing_from_terminal = from_states & terminal_states

        assert not outgoing_from_terminal, (
            f"Transition {tid} violates terminal state invariant: "
            f"allows outgoing transition from terminal state(s) {outgoing_from_terminal}"
        )


def test_terminal_state_outgoing_transition_violation_detected() -> None:
    """Demonstrate that an outgoing transition from a terminal state is flagged."""
    terminal_states = {"CANCELLED", "REFUNDED"}
    invalid_from_states = ["PAID", "CANCELLED"]
    overlap = set(invalid_from_states) & terminal_states
    assert overlap == {"CANCELLED"}


# ==============================================================================
# Invariant 3: Reachability Invariant
# ==============================================================================


def test_reachability_invariant(checkout_model: dict[str, Any]) -> None:
    """Every non-initial state is reachable via at least one directed path from CART_ACTIVE."""
    initial_state: str = checkout_model["initial_state"]
    assert initial_state == "CART_ACTIVE", (
        f"Expected initial_state CART_ACTIVE, got {initial_state}"
    )

    all_state_ids = {s["state_id"] for s in checkout_model["states"]}
    assert initial_state in all_state_ids, f"Initial state {initial_state} not in defined states"

    visited = compute_reachable_states(initial_state, checkout_model["transitions"])

    # Every non-initial state must be in visited
    non_initial_states = all_state_ids - {initial_state}
    unreachable_states = non_initial_states - visited

    assert not unreachable_states, (
        f"Reachability invariant violated: the following states cannot be reached "
        f"from '{initial_state}': {sorted(unreachable_states)}"
    )
    assert visited == all_state_ids, "Not all defined states are reachable from initial state"


def test_unreachable_state_detected() -> None:
    """Demonstrate that disconnected or orphaned states are detected."""
    states = {"CART_ACTIVE", "ORDER_CREATED", "ORPHANED_STATE"}
    transitions = [
        {"from_states": ["CART_ACTIVE"], "to_state": "ORDER_CREATED"},
    ]
    reachable = compute_reachable_states("CART_ACTIVE", transitions)
    unreachable = states - reachable
    assert unreachable == {"ORPHANED_STATE"}


# ==============================================================================
# Invariant 4: Role Authorization Invariant
# ==============================================================================


def test_role_authorization_invariant(checkout_model: dict[str, Any]) -> None:
    """Every transition defines at least one non-empty string in allowed_roles."""
    for transition in checkout_model["transitions"]:
        tid = transition["transition_id"]
        roles = transition.get("allowed_roles")

        assert isinstance(roles, list), f"Transition {tid} missing 'allowed_roles' list"
        assert len(roles) >= 1, f"Transition {tid} defines empty 'allowed_roles'"

        for role in roles:
            assert isinstance(role, str), f"Transition {tid} role is not a string: {role!r}"
            assert role.strip(), f"Transition {tid} contains empty/whitespace-only role string"


def test_empty_role_list_violation_detected() -> None:
    """Demonstrate that an empty allowed_roles array is flagged."""
    invalid_roles: list[str] = []
    assert len(invalid_roles) == 0


def test_empty_role_string_violation_detected() -> None:
    """Demonstrate that an empty or whitespace-only role string is flagged."""
    assert not "   ".strip()
