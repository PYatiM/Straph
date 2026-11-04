"""In-memory model tables for fast O(1) workflow lookups."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ModelTables:
    """Precomputed lookup tables for reference workflow models."""

    source_states: dict[str, set[str]] = field(default_factory=dict)
    target_states: dict[tuple[str, str], str] = field(default_factory=dict)
    one_shot_ops: set[str] = field(default_factory=set)
    allowed_roles: dict[str, set[str]] = field(default_factory=dict)
    reachability: dict[str, set[str]] = field(default_factory=dict)
    shortest_paths: dict[tuple[str, str], list[str]] = field(default_factory=dict)
    initial_state: str = "START"
    terminal_states: set[str] = field(default_factory=set)
    model_version: str = "unknown"

    @classmethod
    def load_tables(cls, model_dict: dict[str, Any]) -> ModelTables:
        """Precompute lookup dictionaries and graph paths from reference model dict."""
        source_states: dict[str, set[str]] = {}
        target_states: dict[tuple[str, str], str] = {}
        one_shot_ops: set[str] = set()
        allowed_roles: dict[str, set[str]] = {}
        reachability: dict[str, set[str]] = {}
        shortest_paths: dict[tuple[str, str], list[str]] = {}

        initial_state = str(model_dict.get("initial_state", "START"))
        model_version = str(model_dict.get("model_version", "unknown"))

        raw_states = model_dict.get("states", [])
        state_ids: set[str] = set()
        terminal_states: set[str] = set()
        for s in raw_states:
            s_id = str(s["state_id"])
            state_ids.add(s_id)
            if s.get("terminal", False):
                terminal_states.add(s_id)

        adj: dict[str, list[tuple[str, str]]] = {s: [] for s in state_ids}

        for t in model_dict.get("transitions", []):
            op = t.get("operation", {})
            method = str(op.get("method", "")).upper()
            path = str(op.get("path_template", ""))
            full_op = f"{method} {path}" if method and path else path
            from_states = [str(st) for st in t.get("from_states", [])]
            to_state = str(t.get("to_state", ""))
            single_use = bool(t.get("single_use", False))
            roles = {str(r) for r in t.get("allowed_roles", [])}

            # Register operation under both full_op and path for fast lookup
            for op_key in {full_op, path}:
                if not op_key:
                    continue
                source_states.setdefault(op_key, set()).update(from_states)
                allowed_roles.setdefault(op_key, set()).update(roles)
                if single_use:
                    one_shot_ops.add(op_key)
                for f_st in from_states:
                    target_states[(f_st, op_key)] = to_state

            # Build adjacency using full_op for canonical edge label
            for f_st in from_states:
                if f_st not in adj:
                    adj[f_st] = []
                adj[f_st].append((to_state, full_op))

        # BFS for reachability and shortest paths between all state pairs
        for start_state in state_ids:
            reachability[start_state] = set()
            shortest_paths[(start_state, start_state)] = []

            queue: deque[tuple[str, list[str]]] = deque([(start_state, [])])
            visited: set[str] = set()

            while queue:
                curr, path_ops = queue.popleft()
                for nxt, op_label in adj.get(curr, []):
                    if nxt not in visited:
                        visited.add(nxt)
                        reachability[start_state].add(nxt)
                        new_path = path_ops + [op_label]
                        if (start_state, nxt) not in shortest_paths:
                            shortest_paths[(start_state, nxt)] = new_path
                        queue.append((nxt, new_path))

        return cls(
            source_states=source_states,
            target_states=target_states,
            one_shot_ops=one_shot_ops,
            allowed_roles=allowed_roles,
            reachability=reachability,
            shortest_paths=shortest_paths,
            initial_state=initial_state,
            terminal_states=terminal_states,
            model_version=model_version,
        )


def load_tables(model_dict: dict[str, Any]) -> ModelTables:
    """Convenience functional loader for ModelTables."""
    return ModelTables.load_tables(model_dict)
