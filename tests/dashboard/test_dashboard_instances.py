"""Unit tests for the instance state inspection dashboard endpoint."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from services.dashboard.app import create_dashboard_app
from services.detector.store import InMemoryInstanceStore, InstanceRecord


@pytest.fixture
def instance_store() -> InMemoryInstanceStore:
    """Provide InMemoryInstanceStore populated with clean and tainted instances."""
    store = InMemoryInstanceStore()

    # 1. Clean order instance
    clean_record = InstanceRecord(
        resource_type="Order",
        resource_id="1001",
        current_state="PAID",
        history=[
            {"operation": "POST /orders", "status": 201},
            {"operation": "PUT /orders/{id}/address", "status": 200},
            {"operation": "POST /orders/{id}/payment", "status": 200},
        ],
        owner_subject="alice",
        tainted=False,
    )
    store.update(clean_record)

    # 2. Tainted order instance
    tainted_record = InstanceRecord(
        resource_type="Order",
        resource_id="1002",
        current_state="FULFILLMENT_QUEUED",
        history=[
            {"operation": "POST /orders", "status": 201},
            {"operation": "POST /orders/{id}/fulfill", "status": 200, "tainted": True},
        ],
        owner_subject="mallory",
        tainted=True,
    )
    store.update(tainted_record)

    return store


@pytest.fixture
def client(instance_store: InMemoryInstanceStore) -> TestClient:
    """Provide test client wired to instance store."""
    app = create_dashboard_app(instance_store=instance_store)
    return TestClient(app)


def test_get_clean_instance_state(client: TestClient) -> None:
    """GET /instances/Order/1001 accurately returns state for clean order."""
    response = client.get("/instances/Order/1001")
    assert response.status_code == 200
    data = response.json()

    assert data["resource_type"] == "Order"
    assert data["resource_id"] == "1001"
    assert data["current_state"] == "PAID"
    assert data["tainted"] is False
    assert data["owner_subject"] == "alice"
    assert data["history_length"] == 3


def test_get_tainted_instance_state(client: TestClient) -> None:
    """GET /instances/Order/1002 accurately returns tainted status and state."""
    response = client.get("/instances/Order/1002")
    assert response.status_code == 200
    data = response.json()

    assert data["resource_type"] == "Order"
    assert data["resource_id"] == "1002"
    assert data["current_state"] == "FULFILLMENT_QUEUED"
    assert data["tainted"] is True
    assert data["owner_subject"] == "mallory"
    assert data["history_length"] == 2


def test_get_missing_instance_returns_404(client: TestClient) -> None:
    """GET /instances/Order/9999 returns 404 Not Found."""
    response = client.get("/instances/Order/9999")
    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()
