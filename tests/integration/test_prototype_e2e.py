"""Full-stack end-to-end prototype integration test for legal checkout and skipped payment attack."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from jsonschema import Draft202012Validator

from services.dashboard.alert_store import SQLiteAlertStore
from services.dashboard.app import create_dashboard_app
from services.detector.worker import DetectorWorker
from services.evaluation.target_app.app import create_app as create_target_app
from services.evaluation.target_app.flaws import Flaws
from services.gateway.app import create_gateway_app
from services.gateway.producer import InMemoryEventProducer

ROOT_DIR = Path(__file__).resolve().parents[2]
ALERT_SCHEMA_PATH = ROOT_DIR / "contracts" / "alert.schema.json"


@pytest.fixture
def alert_validator() -> Draft202012Validator:
    with open(ALERT_SCHEMA_PATH, "r", encoding="utf-8") as f:
        schema = json.load(f)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


@pytest.mark.anyio
async def test_full_stack_prototype_e2e(alert_validator: Draft202012Validator) -> None:
    """Replay legal checkout and skipped payment attack; assert exact 0 and 1 alert respectively."""
    # 1. Initialize SQLite Alert Store and Dashboard API
    alert_store = SQLiteAlertStore()
    dashboard_app = create_dashboard_app(alert_store=alert_store)
    dashboard_transport = httpx.ASGITransport(app=dashboard_app)
    dashboard_client = httpx.AsyncClient(
        transport=dashboard_transport,
        base_url="http://dashboard:8080",
    )

    # 2. Initialize Detector Worker with Alert Store
    detector_worker = DetectorWorker(alert_store=alert_store)

    # Event producer that feeds emitted events directly into detector worker
    class DirectToDetectorProducer(InMemoryEventProducer):
        async def produce_event(self, key: str, event: Any) -> bool:
            await super().produce_event(key, event)
            # Evaluate through detector worker
            detector_worker.process_event(event.model_dump())
            return True

    producer = DirectToDetectorProducer()

    # 3. Boot Target App (with ALLOW_SKIP_PAYMENT flaw enabled) and Gateway
    flaws = Flaws(skip_payment=True)
    target_app = create_target_app(strict=False, flaws=flaws)
    target_transport = httpx.ASGITransport(app=target_app)
    downstream_client = httpx.AsyncClient(
        transport=target_transport,
        base_url="http://target-app:8001",
    )

    gw_app = create_gateway_app(
        target_app_url="http://target-app:8001",
        producer=producer,
        client=downstream_client,
    )
    gw_transport = httpx.ASGITransport(app=gw_app)
    gw_client = httpx.AsyncClient(
        transport=gw_transport,
        base_url="http://gateway:8000",
    )

    async with gw_client, dashboard_client:
        # Replay Order 2001: Legal checkout path
        # 1. Cart
        h_2001 = {"X-User-Sub": "customer-2001"}
        r = await gw_client.post(
            "/cart/items",
            json={"sku": "BOOK-2001", "quantity": 1},
            headers=h_2001,
        )
        assert r.status_code == 200

        # 2. Order
        r = await gw_client.post("/orders", headers=h_2001)
        assert r.status_code == 201
        order_2001_id = r.json()["order_id"]

        # 3. Address
        r = await gw_client.put(
            f"/orders/{order_2001_id}/address",
            json={"address": "1001 High St"},
            headers=h_2001,
        )
        assert r.status_code == 200

        # 4. Payment
        r = await gw_client.post(f"/orders/{order_2001_id}/payment", headers=h_2001)
        assert r.status_code == 200

        # 5. Webhook
        h_gw = {"X-User-Sub": "pg-service", "X-User-Roles": "payment-gateway"}
        r = await gw_client.post(
            "/payments/webhook",
            json={"order_id": order_2001_id},
            headers=h_gw,
        )
        assert r.status_code == 200

        # 6. Fulfill
        h_wh = {"X-User-Sub": "wh-worker", "X-User-Roles": "warehouse"}
        r = await gw_client.post(f"/orders/{order_2001_id}/fulfill", headers=h_wh)
        assert r.status_code == 200

        # 7. Ship
        r = await gw_client.post(f"/orders/{order_2001_id}/ship", headers=h_wh)
        assert r.status_code == 200

        # Replay Order 2002: Skipped payment attack
        # 1. Cart & Order
        h_2002 = {"X-User-Sub": "customer-2002"}
        await gw_client.post(
            "/cart/items",
            json={"sku": "LAPTOP-2002", "quantity": 1},
            headers=h_2002,
        )
        r = await gw_client.post("/orders", headers=h_2002)
        assert r.status_code == 201
        order_2002_id = r.json()["order_id"]

        # 2. Address
        r = await gw_client.put(
            f"/orders/{order_2002_id}/address",
            json={"address": "2002 Low St"},
            headers=h_2002,
        )
        assert r.status_code == 200

        # 3. Fulfill directly (Skipping payment & webhook!)
        r = await gw_client.post(f"/orders/{order_2002_id}/fulfill", headers=h_wh)
        assert r.status_code == 200  # Accepted due to flaw

        # 4. Query Dashboard API for alerts
        dash_resp = await dashboard_client.get("/alerts")
        assert dash_resp.status_code == 200
        all_alerts = dash_resp.json()

        # Check Order 2001 alerts
        order_2001_alerts = [
            a for a in all_alerts if a.get("resource", {}).get("id") == order_2001_id
        ]
        assert len(order_2001_alerts) == 0, (
            f"Expected 0 alerts for legal Order {order_2001_id}, got: {order_2001_alerts}"
        )

        # Check Order 2002 alerts
        order_2002_alerts = [
            a for a in all_alerts if a.get("resource", {}).get("id") == order_2002_id
        ]
        assert len(order_2002_alerts) == 1, (
            f"Expected exactly 1 alert for Order {order_2002_id}, got {len(order_2002_alerts)}"
        )

        alert = order_2002_alerts[0]

        # Verify contract schema conformance
        schema_errors = list(alert_validator.iter_errors(alert))
        assert schema_errors == [], f"Alert violated schema: {schema_errors}"

        # Assert exact fields requested by P064
        expected_missing = ["POST /orders/{id}/payment", "POST /payments/webhook"]
        diff_errors: list[str] = []

        if alert["label"] != "SKIPPED":
            diff_errors.append(f"label: expected 'SKIPPED', got '{alert['label']}'")
        if alert["rule"] != "R2":
            diff_errors.append(f"rule: expected 'R2', got '{alert['rule']}'")
        if alert["severity"] != "accepted":
            diff_errors.append(f"severity: expected 'accepted', got '{alert['severity']}'")
        if alert["missing"] != expected_missing:
            diff_errors.append(
                f"missing operations: expected {expected_missing}, got {alert['missing']}"
            )

        assert not diff_errors, "Alert content differences:\n" + "\n".join(diff_errors)
