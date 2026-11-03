"""FastAPI application for Straph security alerts dashboard."""

from __future__ import annotations

import os
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request

from services.dashboard.alert_store import SQLiteAlertStore


def create_dashboard_app(alert_store: SQLiteAlertStore | None = None) -> FastAPI:
    """Factory for the Dashboard FastAPI application."""
    store = alert_store or SQLiteAlertStore(os.getenv("ALERTS_DB_PATH", ":memory:"))

    app = FastAPI(title="Straph Security Dashboard API")
    app.state.alert_store = store

    @app.get("/health", include_in_schema=False)
    def health() -> dict[str, str]:
        return {"status": "healthy", "service": "straph-dashboard"}

    @app.get("/alerts")
    def list_alerts(
        request: Request,
        resource_id: str | None = Query(default=None),
        label: str | None = Query(default=None),
    ) -> list[dict[str, Any]]:
        s: SQLiteAlertStore = request.app.state.alert_store
        return s.get_alerts(resource_id=resource_id, label=label)

    @app.get("/alerts/{alert_id}")
    def get_alert(alert_id: str, request: Request) -> dict[str, Any]:
        s: SQLiteAlertStore = request.app.state.alert_store
        alert = s.get_alert(alert_id)
        if alert is None:
            raise HTTPException(status_code=404, detail="Alert not found")
        return alert

    return app


app = create_dashboard_app()
