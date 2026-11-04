"""Unit tests for server-side rendered HTML alert triage dashboard view."""

from __future__ import annotations

import copy
import re

import pytest
from fastapi.testclient import TestClient

from services.dashboard.alert_store import SQLiteAlertStore
from services.dashboard.app import create_dashboard_app
from tests.detector.fixtures.tc2_skipped_payment import get_tc2_expected_alerts


@pytest.fixture
def populated_ui_store() -> SQLiteAlertStore:
    """Provide SQLiteAlertStore with varied alerts including tainted and XSS test vectors."""
    store = SQLiteAlertStore(":memory:")

    # 1. Accepted, tainted alert with rank_score
    alert1 = copy.deepcopy(get_tc2_expected_alerts()[0])
    alert1["alert_id"] = "11111111-1111-4111-8111-111111111111"
    alert1["severity"] = "accepted"
    alert1["flags"]["tainted"] = True
    alert1["rank_score"] = 0.885
    store.save_alert(alert1)

    # 2. Attempted alert (not tainted)
    alert2 = copy.deepcopy(get_tc2_expected_alerts()[0])
    alert2["alert_id"] = "22222222-2222-4222-8222-222222222222"
    alert2["severity"] = "attempted"
    alert2["flags"]["tainted"] = False
    alert2["rank_score"] = 0.420
    store.save_alert(alert2)

    # 3. Alert with potentially malicious XSS payload in dynamic fields
    alert_xss = copy.deepcopy(get_tc2_expected_alerts()[0])
    alert_xss["alert_id"] = "33333333-3333-4333-8333-333333333333"
    alert_xss["resource"]["id"] = '<script>alert("xss")</script>'
    alert_xss["severity"] = "attempted"
    alert_xss["rank_score"] = 0.100
    store.save_alert(alert_xss)

    return store


@pytest.fixture
def client(populated_ui_store: SQLiteAlertStore) -> TestClient:
    """Create test client for UI tests."""
    app = create_dashboard_app(alert_store=populated_ui_store)
    return TestClient(app)


def test_ui_table_structure_and_headers(client: TestClient) -> None:
    """GET /ui returns HTML containing table with required columns."""
    response = client.get("/ui")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    html = response.text

    expected_headers = [
        "ALERT ID",
        "TIMESTAMP",
        "LABEL",
        "RULE",
        "SEVERITY",
        "RESOURCE",
        "OWASP CLASS",
        "RANK SCORE",
    ]
    for header in expected_headers:
        assert header in html, f"Header '{header}' missing from HTML table."


def test_ui_badges_rendering(client: TestClient) -> None:
    """HTML triage view must render badge-accepted, badge-attempted, and badge-tainted."""
    response = client.get("/ui")
    assert response.status_code == 200
    html = response.text

    assert "badge-accepted" in html
    assert "badge-attempted" in html
    assert "badge-tainted" in html


def test_ui_filter_bar_elements(client: TestClient) -> None:
    """Filter bar must render dropdowns for Label, Rule, and Min Rank."""
    response = client.get("/ui")
    html = response.text

    assert '<select id="label-select"' in html
    assert '<select id="rule-select"' in html
    assert '<select id="rank-select"' in html
    assert "SKIPPED" in html
    assert "OUT_OF_ORDER" in html
    assert "R0" in html
    assert "R2" in html


def test_xss_escaping_prevention(client: TestClient) -> None:
    """Dynamic fields containing script tags must be strictly HTML-escaped."""
    response = client.get("/ui")
    html = response.text

    # Raw script tag must NOT exist unescaped in HTML body
    assert '<script>alert("xss")</script>' not in html
    # Must be properly escaped to entities
    assert (
        "&lt;script&gt;alert(&#34;xss&#34;)&lt;/script&gt;" in html
        or '&lt;script&gt;alert("xss")&lt;/script&gt;' in html
    )


def test_zero_external_cdn_dependencies(client: TestClient) -> None:
    """HTML must not load external CSS/JS libraries from CDNs."""
    response = client.get("/ui")
    html = response.text

    # Check for external script or link stylesheet tags referencing external hosts
    external_scripts = re.findall(r'<script[^>]+src=["\'](http[s]?://[^"\']+)["\']', html)
    external_links = re.findall(r'<link[^>]+href=["\'](http[s]?://[^"\']+)["\']', html)

    assert external_scripts == [], f"Found external scripts: {external_scripts}"
    assert external_links == [], f"Found external stylesheets: {external_links}"
