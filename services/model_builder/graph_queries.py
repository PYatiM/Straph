"""Graph queries for workflow navigation, legal transitions, and reachability."""

from __future__ import annotations

from collections import deque
from typing import Any

from services.model_builder.neo4j_loader import InMemoryGraphDriver, InMemoryGraphSession

CYPHER_LEGAL_TRANSITIONS = """
MATCH (s:State {id: $current_state})-[:OUTGOING]->(t:Transition)-[:TARGET]->(target:State)
RETURN t.id AS transition_id,
       t.operation AS operation,
       t.allowed_roles AS allowed_roles,
       t.single_use AS single_use,
       target.id AS to_state
"""

CYPHER_SOURCE_STATES = """
MATCH (s:State)-[:OUTGOING]->(t:Transition)
WHERE t.operation = $operation_id OR t.id = $operation_id
RETURN s.id AS source_state
"""

CYPHER_SHORTEST_PATH = """
MATCH p = shortestPath((s1:State {id: $from_state})-[:NEXT*..10]->(s2:State {id: $to_state}))
RETURN [r IN relationships(p) | r.operation] AS operations
"""

CYPHER_IS_REACHABLE = """
MATCH p = shortestPath((s1:State {id: $from_state})-[:NEXT*..20]->(s2:State {id: $target_state}))
RETURN count(p) > 0 AS reachable
"""


def _get_driver_instance(driver_or_session: Any) -> InMemoryGraphDriver | None:
    if isinstance(driver_or_session, InMemoryGraphDriver):
        return driver_or_session
    if isinstance(driver_or_session, InMemoryGraphSession):
        return driver_or_session.driver
    return None


def get_legal_transitions(driver: Any, current_state: str) -> list[dict[str, Any]]:
    """Return all outgoing transitions and allowed roles from current_state."""
    mem_driver = _get_driver_instance(driver)
    if mem_driver is not None:
        transitions_dict = mem_driver.get_transitions()
        outgoing_rels = [
            r
            for r in mem_driver.get_relationships("OUTGOING")
            if r.get("from_state") == current_state
        ]
        results: list[dict[str, Any]] = []
        for rel in outgoing_rels:
            t_id = rel.get("transition_id")
            if not t_id or t_id not in transitions_dict:
                continue
            tr = transitions_dict[t_id]
            target_rel = next(
                (
                    r
                    for r in mem_driver.get_relationships("TARGET")
                    if r.get("transition_id") == t_id
                ),
                None,
            )
            to_state = target_rel.get("to_state") if target_rel else None
            results.append(
                {
                    "transition_id": t_id,
                    "operation": tr.get("operation", ""),
                    "allowed_roles": list(tr.get("allowed_roles", [])),
                    "single_use": bool(tr.get("single_use", False)),
                    "to_state": to_state,
                }
            )
        return results

    # Fallback to real Neo4j session/driver
    session = driver.session() if hasattr(driver, "session") else driver
    res = session.run(CYPHER_LEGAL_TRANSITIONS, {"current_state": current_state})
    records = res.data() if hasattr(res, "data") else [dict(r) for r in res]
    return [
        {
            "transition_id": r["transition_id"],
            "operation": r["operation"],
            "allowed_roles": list(r["allowed_roles"]),
            "single_use": bool(r.get("single_use", False)),
            "to_state": r["to_state"],
        }
        for r in records
    ]


def get_source_states(driver: Any, operation_id: str) -> set[str]:
    """Return all states from which operation is legal."""
    mem_driver = _get_driver_instance(driver)
    if mem_driver is not None:
        matching_t_ids = {
            t_id
            for t_id, tr in mem_driver.get_transitions().items()
            if tr.get("operation") == operation_id or t_id == operation_id
        }
        sources: set[str] = set()
        for rel in mem_driver.get_relationships("OUTGOING"):
            if rel.get("transition_id") in matching_t_ids:
                fs = rel.get("from_state")
                if fs:
                    sources.add(fs)
        return sources

    session = driver.session() if hasattr(driver, "session") else driver
    res = session.run(CYPHER_SOURCE_STATES, {"operation_id": operation_id})
    records = res.data() if hasattr(res, "data") else [dict(r) for r in res]
    return {r["source_state"] for r in records}


def get_shortest_path(
    driver: Any,
    from_state: str,
    to_state: str,
    max_depth: int = 10,
) -> list[str]:
    """Execute BFS traversal over NEXT edges returning the ordered list of missing operations."""
    if from_state == to_state:
        return []

    mem_driver = _get_driver_instance(driver)
    if mem_driver is not None:
        next_rels = mem_driver.get_relationships("NEXT")
        # Build adjacency: from_state -> list of (to_state, operation)
        adj: dict[str, list[tuple[str, str]]] = {}
        for r in next_rels:
            fs = r.get("from_state")
            ts = r.get("to_state")
            op = r.get("operation", "")
            if fs and ts:
                adj.setdefault(fs, []).append((ts, op))

        queue: deque[tuple[str, list[str]]] = deque([(from_state, [])])
        visited: set[str] = {from_state}

        while queue:
            curr_state, path = queue.popleft()
            if len(path) >= max_depth:
                continue

            for next_st, op in adj.get(curr_state, []):
                new_path = [*path, op]
                if next_st == to_state:
                    return new_path
                if next_st not in visited:
                    visited.add(next_st)
                    queue.append((next_st, new_path))
        return []

    session = driver.session() if hasattr(driver, "session") else driver
    res = session.run(
        CYPHER_SHORTEST_PATH,
        {"from_state": from_state, "to_state": to_state},
    )
    rec = res.single() if hasattr(res, "single") else None
    if rec and rec.get("operations"):
        return list(rec.get("operations"))
    return []


def is_state_reachable(driver: Any, from_state: str, target_state: str) -> bool:
    """Return True if target_state is reachable ahead from from_state."""
    if from_state == target_state:
        return True

    mem_driver = _get_driver_instance(driver)
    if mem_driver is not None:
        next_rels = mem_driver.get_relationships("NEXT")
        adj: dict[str, list[str]] = {}
        for r in next_rels:
            fs = r.get("from_state")
            ts = r.get("to_state")
            if fs and ts:
                adj.setdefault(fs, []).append(ts)

        queue: deque[str] = deque([from_state])
        visited: set[str] = {from_state}

        while queue:
            curr = queue.popleft()
            for nxt in adj.get(curr, []):
                if nxt == target_state:
                    return True
                if nxt not in visited:
                    visited.add(nxt)
                    queue.append(nxt)
        return False

    session = driver.session() if hasattr(driver, "session") else driver
    res = session.run(
        CYPHER_IS_REACHABLE,
        {"from_state": from_state, "target_state": target_state},
    )
    rec = res.single() if hasattr(res, "single") else None
    return bool(rec.get("reachable")) if rec else False
