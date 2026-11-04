"""Unit tests for the alert explanation detail view with path diffs."""

from __future__ import annotations

import copy

import pytest
from fastapi.testclient import TestClient

from services.dashboard.alert_store import SQLiteAlertStore
from services.dashboard.app import create_dashboard_app
from tests.detector.fixtures.tc2_skipped_payment import get_tc2_expected_alerts


@pytest.fixture
def detail_store() -> SQLiteAlertStore:
    """Provide store populated with TC2 skipped-payment alert."""
    store = SQLiteAlertStore(":memory:")
    alert = copy.deepcopy(get_tc2_expected_alerts()[0])
    store.save_alert(alert)
    return store


@pytest.fixture
def client(detail_store: SQLiteAlertStore) -> TestClient:
    """Provide test client for detail view."""
    app = create_dashboard_app(alert_store=detail_store)
    return TestClient(app)


def test_tc2_detail_view_rendering(client: TestClient) -> None:
    """Detail view must render header, expected breadcrumbs, observed grid, and missing operations."""
    tc2_alert = get_tc2_expected_alerts()[0]
    alert_id = tc2_alert["alert_id"]

    response = client.get(f"/ui/alerts/{alert_id}")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    html = response.text

    # 1. Header: Alert ID, Rule, OWASP Category, Severity
    assert alert_id in html
    assert "R2" in html
    assert "SKIPPED" in html
    assert "OWASP Category:" in html
    assert "BLA6" in html
    assert "badge-accepted" in html

    # 2. Section Expected Path: breadcrumbs
    assert "Expected Path" in html
    assert "breadcrumbs-container" in html
    assert "POST /orders/{id}/payment" in html

    # 3. Section Observed: offending op, state, status code
    assert "Observed" in html
    assert "POST /orders/{id}/fulfill" in html
    assert "ADDRESS_SET" in html
    assert "200" in html

    # 4. Section Missing Operations: highlighted in red box with warning icon
    assert "Missing Operations" in html
    assert "missing-box" in html
    assert "warning-icon" in html
    assert "POST /orders/{id}/payment" in html
    assert "POST /payments/webhook" in html


def test_detail_view_not_found(client: TestClient) -> None:
    """GET /ui/alerts/{unknown_id} returns 404."""
    response = client.get("/ui/alerts/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404
    assert "alert not found" in response.json()["detail"].lower()
