"""Neo4j model loader and in-memory graph driver for reference workflow models."""

from __future__ import annotations

import json
from pathlib import Path
from types import TracebackType
from typing import Any, Protocol, Self

try:
    from jsonschema import Draft7Validator
except ImportError:  # pragma: no cover
    Draft7Validator = None  # type: ignore[assignment,misc]

STATE_CONSTRAINT_QUERY = (
    "CREATE CONSTRAINT state_id_unique IF NOT EXISTS FOR (s:State) REQUIRE s.state_id IS UNIQUE"
)

TRANSITION_CONSTRAINT_QUERY = (
    "CREATE CONSTRAINT transition_id_unique IF NOT EXISTS "
    "FOR (t:Transition) REQUIRE t.transition_id IS UNIQUE"
)

LOAD_STATES_QUERY = """
UNWIND $states AS state
MERGE (s:State {id: state.id})
SET s.state_id = state.id,
    s.terminal = state.terminal,
    s.description = state.description
"""

LOAD_TRANSITIONS_QUERY = """
UNWIND $transitions AS t
MERGE (tr:Transition {id: t.id})
SET tr.transition_id = t.id,
    tr.operation = t.operation,
    tr.single_use = t.single_use,
    tr.provenance = t.provenance,
    tr.support_count = t.support_count,
    tr.allowed_roles = t.allowed_roles,
    tr.review_status = t.review_status,
    tr.method = t.method,
    tr.path_template = t.path_template
"""

LOAD_OUTGOING_QUERY = """
UNWIND $outgoing AS link
MATCH (s:State {id: link.from_state})
MATCH (t:Transition {id: link.transition_id})
MERGE (s)-[:OUTGOING]->(t)
"""

LOAD_TARGET_QUERY = """
UNWIND $targets AS link
MATCH (t:Transition {id: link.transition_id})
MATCH (s:State {id: link.to_state})
MERGE (t)-[:TARGET]->(s)
"""

LOAD_NEXT_QUERY = """
UNWIND $nexts AS link
MATCH (s1:State {id: link.from_state})
MATCH (s2:State {id: link.to_state})
MERGE (s1)-[r:NEXT {operation: link.operation}]->(s2)
"""


class CypherSession(Protocol):
    """Protocol representing a Neo4j session capable of executing Cypher queries."""

    def run(
        self,
        query: str,
        parameters: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> Any:
        """Run a parameterized Cypher query."""
        ...


class InMemoryRecord:
    """Mock record imitating neo4j.Record."""

    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def data(self) -> dict[str, Any]:
        return dict(self._data)


class InMemoryResult:
    """Mock result imitating neo4j.Result."""

    def __init__(self, records: list[dict[str, Any]]) -> None:
        self._records = [InMemoryRecord(r) for r in records]
        self._idx = 0

    def __iter__(self) -> InMemoryResult:
        return self

    def __next__(self) -> InMemoryRecord:
        if self._idx < len(self._records):
            rec = self._records[self._idx]
            self._idx += 1
            return rec
        raise StopIteration

    def data(self) -> list[dict[str, Any]]:
        return [r.data() for r in self._records]

    def single(self) -> InMemoryRecord | None:
        return self._records[0] if self._records else None


class InMemoryGraphSession:
    """Session for in-memory graph driver mimicking neo4j.Session."""

    def __init__(self, driver: InMemoryGraphDriver) -> None:
        self.driver = driver
        self.closed: bool = False

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        self.closed = True

    def run(
        self,
        query: str,
        parameters: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> InMemoryResult:
        params: dict[str, Any] = parameters or {}
        q = query.strip()

        if q.startswith("CREATE CONSTRAINT"):
            if q not in self.driver.constraints:
                self.driver.constraints.append(q)
            return InMemoryResult([])

        if "UNWIND $states AS state" in q:
            states_list = params.get("states", [])
            for st in states_list:
                s_id = st["id"]
                existing = self.driver.nodes["State"].setdefault(s_id, {})
                existing.update(st)
            return InMemoryResult([])

        if "UNWIND $transitions AS t" in q:
            trans_list = params.get("transitions", [])
            for tr in trans_list:
                t_id = tr["id"]
                existing = self.driver.nodes["Transition"].setdefault(t_id, {})
                existing.update(tr)
            return InMemoryResult([])

        if "UNWIND $outgoing AS link" in q:
            out_list = params.get("outgoing", [])
            for item in out_list:
                rel = {
                    "type": "OUTGOING",
                    "from_state": item["from_state"],
                    "transition_id": item["transition_id"],
                }
                if rel not in self.driver.relationships:
                    self.driver.relationships.append(rel)
            return InMemoryResult([])

        if "UNWIND $targets AS link" in q:
            tgt_list = params.get("targets", [])
            for item in tgt_list:
                rel = {
                    "type": "TARGET",
                    "transition_id": item["transition_id"],
                    "to_state": item["to_state"],
                }
                if rel not in self.driver.relationships:
                    self.driver.relationships.append(rel)
            return InMemoryResult([])

        if "UNWIND $nexts AS link" in q:
            nxt_list = params.get("nexts", [])
            for item in nxt_list:
                rel = {
                    "type": "NEXT",
                    "from_state": item["from_state"],
                    "to_state": item["to_state"],
                    "operation": item["operation"],
                }
                if rel not in self.driver.relationships:
                    self.driver.relationships.append(rel)
            return InMemoryResult([])

        return InMemoryResult([])


class InMemoryGraphDriver:
    """Mock Neo4j Driver for testing without a running Neo4j daemon."""

    def __init__(self) -> None:
        self.nodes: dict[str, dict[str, dict[str, Any]]] = {
            "State": {},
            "Transition": {},
        }
        self.relationships: list[dict[str, Any]] = []
        self.constraints: list[str] = []
        self.closed: bool = False

    def session(self, **kwargs: Any) -> InMemoryGraphSession:
        return InMemoryGraphSession(self)

    def close(self) -> None:
        self.closed = True

    def get_states(self) -> dict[str, dict[str, Any]]:
        return dict(self.nodes["State"])

    def get_transitions(self) -> dict[str, dict[str, Any]]:
        return dict(self.nodes["Transition"])

    def get_relationships(
        self,
        rel_type: str | None = None,
    ) -> list[dict[str, Any]]:
        if rel_type is None:
            return list(self.relationships)
        return [r for r in self.relationships if r.get("type") == rel_type]


class Neo4jModelLoader:
    """Loads reference workflow models into Neo4j using parameterized Cypher."""

    def __init__(self, schema_path: str | Path | None = None) -> None:
        if schema_path is None:
            schema_path = Path(__file__).resolve().parents[2] / "contracts" / "model.schema.json"
        self.schema_path = Path(schema_path)
        self._validator = None
        if self.schema_path.exists() and Draft7Validator is not None:
            with open(self.schema_path, "r", encoding="utf-8") as f:
                schema = json.load(f)
            self._validator = Draft7Validator(schema)

    def apply_constraints(self, session: CypherSession) -> None:
        """Enforce unique constraints on State(state_id) and Transition(transition_id)."""
        session.run(STATE_CONSTRAINT_QUERY)
        session.run(TRANSITION_CONSTRAINT_QUERY)

    def validate_model_dict(self, model_dict: dict[str, Any]) -> None:
        """Validate model_dict against contracts/model.schema.json if available."""
        if self._validator is not None:
            self._validator.validate(model_dict)
        else:
            required = ["resource_type", "states", "transitions"]
            for field in required:
                if field not in model_dict:
                    msg = f"Missing required field: {field}"
                    raise ValueError(msg)

    def load_model(
        self,
        session: CypherSession,
        model_dict: dict[str, Any],
    ) -> dict[str, int]:
        """Parse states and transitions and load them idempotently via Cypher."""
        self.validate_model_dict(model_dict)

        raw_states: list[dict[str, Any]] = model_dict.get("states", [])
        raw_transitions: list[dict[str, Any]] = model_dict.get("transitions", [])

        states_params: list[dict[str, Any]] = [
            {
                "id": s["state_id"],
                "state_id": s["state_id"],
                "terminal": bool(s.get("terminal", False)),
                "description": str(s.get("description", "")),
            }
            for s in raw_states
        ]

        transitions_params: list[dict[str, Any]] = []
        outgoing_params: list[dict[str, Any]] = []
        target_params: list[dict[str, Any]] = []
        next_params: list[dict[str, Any]] = []

        for t in raw_transitions:
            t_id = t["transition_id"]
            op = t["operation"]
            op_str = f"{op['method']} {op['path_template']}"
            single_use = bool(t.get("single_use", False))
            provenance = str(t.get("provenance", "spec"))
            support_count = int(t.get("support_count", 0))
            allowed_roles = list(t.get("allowed_roles", []))
            review_status = str(t.get("review_status", "confirmed"))

            transitions_params.append(
                {
                    "id": t_id,
                    "transition_id": t_id,
                    "operation": op_str,
                    "single_use": single_use,
                    "provenance": provenance,
                    "support_count": support_count,
                    "allowed_roles": allowed_roles,
                    "review_status": review_status,
                    "method": op.get("method"),
                    "path_template": op.get("path_template"),
                }
            )

            to_state = t["to_state"]
            target_params.append(
                {
                    "transition_id": t_id,
                    "to_state": to_state,
                }
            )

            for fs in t.get("from_states", []):
                outgoing_params.append(
                    {
                        "from_state": fs,
                        "transition_id": t_id,
                    }
                )
                next_params.append(
                    {
                        "from_state": fs,
                        "to_state": to_state,
                        "operation": op_str,
                        "transition_id": t_id,
                    }
                )

        session.run(LOAD_STATES_QUERY, {"states": states_params})
        session.run(LOAD_TRANSITIONS_QUERY, {"transitions": transitions_params})
        session.run(LOAD_OUTGOING_QUERY, {"outgoing": outgoing_params})
        session.run(LOAD_TARGET_QUERY, {"targets": target_params})
        session.run(LOAD_NEXT_QUERY, {"nexts": next_params})

        return {
            "states_loaded": len(states_params),
            "transitions_loaded": len(transitions_params),
        }
