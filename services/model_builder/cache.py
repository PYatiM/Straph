"""In-memory reference workflow graph cache for high-throughput O(1) queries."""

from __future__ import annotations

from collections import deque
from typing import Any


class InMemoryModelGraph:
    """Precomputed in-memory representation of reference workflow model.

    Provides O(1) dictionary lookups for legal transitions, reachability,
    and missing shortest paths, eliminating per-event network roundtrips.
    """

    def __init__(self, model_dict: dict[str, Any] | None = None) -> None:
        self.resource_type: str = ""
        self.initial_state: str = ""
        self.model_version: str = ""
        self.states: dict[str, dict[str, Any]] = {}
        self.transitions: list[dict[str, Any]] = []

        # O(1) lookup tables
        self.legal_transitions: dict[tuple[str, str], list[str]] = {}
        self.reachability_set: dict[str, set[str]] = {}
        self.shortest_paths: dict[tuple[str, str], list[str]] = {}
        self.allowed_roles_map: dict[tuple[str, str], list[str]] = {}
        self.single_use_map: dict[str, bool] = {}

        if model_dict is not None:
            self.load_from_dict(model_dict)

    def load_from_dict(self, model_dict: dict[str, Any]) -> None:
        """Parse model JSON into adjacency dicts and precompute graph queries."""
        self.resource_type = str(model_dict.get("resource_type", ""))
        self.initial_state = str(model_dict.get("initial_state", ""))
        self.model_version = str(model_dict.get("model_version", ""))
        self.transitions = list(model_dict.get("transitions", []))

        # Clear existing tables
        self.states.clear()
        self.legal_transitions.clear()
        self.reachability_set.clear()
        self.shortest_paths.clear()
        self.allowed_roles_map.clear()
        self.single_use_map.clear()

        for s in model_dict.get("states", []):
            s_id = s["state_id"]
            self.states[s_id] = dict(s)

        # Build adjacency graph: from_state -> list of (to_state, operation)
        adj: dict[str, list[tuple[str, str]]] = {s: [] for s in self.states}

        for t in self.transitions:
            t_id = t.get("transition_id", "")
            op = t["operation"]
            op_str = f"{op['method']} {op['path_template']}"
            to_st = t["to_state"]
            single_use = bool(t.get("single_use", False))
            roles = list(t.get("allowed_roles", []))

            self.single_use_map[t_id] = single_use
            self.single_use_map[op_str] = single_use

            for fs in t.get("from_states", []):
                key = (fs, op_str)
                if key not in self.legal_transitions:
                    self.legal_transitions[key] = []
                if to_st not in self.legal_transitions[key]:
                    self.legal_transitions[key].append(to_st)

                self.allowed_roles_map[key] = roles
                adj.setdefault(fs, []).append((to_st, op_str))

        # 1. Precompute Reachability Set for every state using BFS
        for s_id in self.states:
            reachable: set[str] = {s_id}
            queue: deque[str] = deque([s_id])
            while queue:
                curr = queue.popleft()
                for nxt, _ in adj.get(curr, []):
                    if nxt not in reachable:
                        reachable.add(nxt)
                        queue.append(nxt)
            self.reachability_set[s_id] = reachable

        # 2. Precompute All-Pairs Shortest Paths using BFS
        for start_state in self.states:
            self.shortest_paths[(start_state, start_state)] = []
            queue_paths: deque[tuple[str, list[str]]] = deque([(start_state, [])])
            visited: set[str] = {start_state}

            while queue_paths:
                curr, path = queue_paths.popleft()
                for nxt, op_str in adj.get(curr, []):
                    new_path = [*path, op_str]
                    pair_key = (start_state, nxt)
                    if pair_key not in self.shortest_paths:
                        self.shortest_paths[pair_key] = new_path
                    if nxt not in visited:
                        visited.add(nxt)
                        queue_paths.append((nxt, new_path))

    def get_legal_next_states(self, from_state: str, op_template: str) -> list[str]:
        """O(1) lookup of target states legal for op_template from from_state."""
        return self.legal_transitions.get((from_state, op_template), [])

    def is_state_reachable(self, from_state: str, target_state: str) -> bool:
        """O(1) lookup checking if target_state is reachable ahead from from_state."""
        if from_state == target_state:
            return True
        return target_state in self.reachability_set.get(from_state, set())

    def get_shortest_path(self, from_state: str, to_state: str) -> list[str]:
        """O(1) lookup of missing operations along the shortest path."""
        return self.shortest_paths.get((from_state, to_state), [])

    def get_allowed_roles(self, from_state: str, op_template: str) -> list[str]:
        """O(1) lookup of allowed roles for a transition."""
        return self.allowed_roles_map.get((from_state, op_template), [])

    def is_single_use(self, operation_or_id: str) -> bool:
        """O(1) lookup checking if an operation or transition is single-use."""
        return self.single_use_map.get(operation_or_id, False)

    def get_source_states(self, op_template: str) -> set[str]:
        """Return all states from which op_template is a valid transition."""
        return {fs for (fs, op) in self.legal_transitions if op == op_template}
