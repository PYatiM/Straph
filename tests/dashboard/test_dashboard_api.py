"""Unit and contract tests for read-only FastAPI Dashboard API."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from services.dashboard.alert_store import SQLiteAlertStore
from services.dashboard.app import create_dashboard_app
from tests.detector.fixtures.tc2_skipped_payment import get_tc2_expected_alerts
from tests.detector.fixtures.tc4_double_refund import get_tc4_expected_alerts

ROOT: Path = Path(__file__).resolve().parents[2]
ALERT_SCHEMA_PATH: Path = ROOT / "contracts" / "alert.schema.json"


@pytest.fixture
def alert_schema() -> dict[str, Any]:
    """Load alert schema."""
    return json.loads(ALERT_SCHEMA_PATH.read_text(encoding="utf-8"))


@pytest.fixture
def populated_store() -> SQLiteAlertStore:
    """Provide in-memory store populated with varied test alerts."""
    store = SQLiteAlertStore(":memory:")

    # Alert 1: TC2 R2 SKIPPED accepted with rank 0.85
    tc2_alert = copy.deepcopy(get_tc2_expected_alerts()[0])
    tc2_alert["rank_score"] = 0.85
    store.save_alert(tc2_alert)

    # Alert 2: TC4 R1 REPLAYED accepted with rank 0.95
    tc4_alert = copy.deepcopy(get_tc4_expected_alerts()[0])
    tc4_alert["rank_score"] = 0.95
    store.save_alert(tc4_alert)

    # Alert 3: Synthetic R4 UNMODELED attempted with rank 0.25
    r4_alert = {
        "alert_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        "event_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        "timestamp": "2026-11-04T12:00:00Z",
        "label": "UNMODELED",
        "rule": "R4",
        "severity": "attempted",
        "subject": {"issuer": "auth", "sub": "u2", "role": "customer"},
        "resource": {"type": "Order", "id": "9999"},
        "expected_path": [],
        "observed": {"operation": "POST /unknown", "state": None, "status_code": 404},
        "missing": [],
        "owasp_class": "BLA6",
        "flags": {"tainted": False, "late": False, "retry": False},
        "model_version": "v1.0.0",
        "rank_score": 0.25,
    }
    store.save_alert(r4_alert)

    return store


@pytest.fixture
def client(populated_store: SQLiteAlertStore, alert_schema: dict[str, Any]) -> TestClient:
    """Provide FastAPI test client."""
    app = create_dashboard_app(alert_store=populated_store, schema=alert_schema)
    return TestClient(app)


def test_health_endpoint(client: TestClient) -> None:
    """GET /health must return status healthy."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}


def test_list_all_alerts(client: TestClient) -> None:
    """GET /alerts returns all stored alerts ordered by rank_score DESC."""
    response = client.get("/alerts")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 3
    # Check descending rank_score order: 0.95, 0.85, 0.25
    ranks = [a["rank_score"] for a in data]
    assert ranks == sorted(ranks, reverse=True)


def test_filter_by_rule(client: TestClient) -> None:
    """GET /alerts?rule=R2 filters strictly by rule ID."""
    response = client.get("/alerts", params={"rule": "R2"})
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["rule"] == "R2"
    assert data[0]["label"] == "SKIPPED"


def test_filter_by_label(client: TestClient) -> None:
    """GET /alerts?label=REPLAYED filters strictly by violation label."""
    response = client.get("/alerts", params={"label": "REPLAYED"})
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["label"] == "REPLAYED"
    assert data[0]["rule"] == "R1"


def test_filter_by_severity(client: TestClient) -> None:
    """GET /alerts?severity=accepted filters by severity level."""
    response = client.get("/alerts", params={"severity": "accepted"})
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    for alert in data:
        assert alert["severity"] == "accepted"


def test_filter_by_min_rank(client: TestClient) -> None:
    """GET /alerts?min_rank=0.8 filters alerts above rank threshold."""
    response = client.get("/alerts", params={"min_rank": 0.8})
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    for alert in data:
        assert alert["rank_score"] >= 0.8


def test_pagination_limit(client: TestClient) -> None:
    """GET /alerts?limit=1 limits returned array length."""
    response = client.get("/alerts", params={"limit": 1})
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["rank_score"] == 0.95


def test_get_single_alert_found_and_not_found(client: TestClient) -> None:
    """GET /alerts/{alert_id} returns alert or 404."""
    # Existing alert
    tc2_id = get_tc2_expected_alerts()[0]["alert_id"]
    res_ok = client.get(f"/alerts/{tc2_id}")
    assert res_ok.status_code == 200
    assert res_ok.json()["alert_id"] == tc2_id

    # Non-existent alert
    res_404 = client.get("/alerts/00000000-0000-0000-0000-000000000000")
    assert res_404.status_code == 404
    assert res_404.json()["detail"] == "Alert not found"


def test_read_only_protection_zero_write_endpoints(client: TestClient) -> None:
    """POST/PUT/DELETE on /alerts or /alerts/{id} must be rejected with 405 Method Not Allowed."""
    assert client.post("/alerts", json={}).status_code == 405
    assert client.put("/alerts", json={}).status_code == 405
    assert client.delete("/alerts").status_code == 405
    assert client.post("/alerts/123", json={}).status_code == 405
    assert client.delete("/alerts/123").status_code == 405
