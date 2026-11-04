"""FastAPI application for Straph security alerts dashboard."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

import jsonschema
from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from services.dashboard.alert_store import SQLiteAlertStore
from services.dashboard.filters import AlertFilterEngine
from services.detector.store import InMemoryInstanceStore

log = logging.getLogger("straph.dashboard.app")

ROOT_DIR = Path(__file__).resolve().parents[2]
ALERT_SCHEMA_PATH = ROOT_DIR / "contracts" / "alert.schema.json"
TEMPLATES_DIR = Path(__file__).parent / "templates"

__all__ = [
    "app",
    "create_dashboard_app",
]


def _load_alert_schema() -> dict[str, Any]:
    """Load alert schema for response contract validation."""
    if ALERT_SCHEMA_PATH.exists():
        with open(ALERT_SCHEMA_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def create_dashboard_app(
    alert_store: SQLiteAlertStore | None = None,
    instance_store: InMemoryInstanceStore | None = None,
    schema: dict[str, Any] | None = None,
) -> FastAPI:
    """Factory for the read-only Dashboard FastAPI application."""
    store = alert_store or SQLiteAlertStore(os.getenv("ALERTS_DB_PATH", ":memory:"))
    instances = instance_store or InMemoryInstanceStore()
    alert_schema = schema or _load_alert_schema()
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

    app = FastAPI(
        title="Straph Security Dashboard API",
        description="Read-only security alert and instance state inspection API",
    )
    app.state.alert_store = store
    app.state.instance_store = instances
    app.state.alert_schema = alert_schema
    app.state.templates = templates

    def _validate_alert(alert: dict[str, Any]) -> None:
        """Validate alert against contracts/alert.schema.json before sending."""
        if app.state.alert_schema:
            try:
                jsonschema.validate(instance=alert, schema=app.state.alert_schema)
            except jsonschema.ValidationError as err:
                log.error("Outgoing alert failed contract validation: %s", err.message)
                raise HTTPException(
                    status_code=500,
                    detail=f"Outgoing alert contract validation failed: {err.message}",
                ) from err

    @app.get("/health")
    def health() -> dict[str, str]:
        """Health check endpoint."""
        return {"status": "healthy"}

    @app.get("/alerts")
    def list_alerts(
        request: Request,
        label: str | None = Query(default=None, description="Filter by violation label"),
        rule: str | None = Query(default=None, description="Filter by rule ID"),
        severity: str | None = Query(default=None, description="Filter by severity level"),
        min_rank: float | None = Query(default=None, description="Minimum ML priority rank score"),
        limit: int = Query(default=50, ge=1, le=1000, description="Max alerts to return"),
    ) -> list[dict[str, Any]]:
        """Query detection alerts filtered by criteria and validated against contract."""
        s: SQLiteAlertStore = request.app.state.alert_store
        alerts = s.get_alerts(
            label=label,
            rule=rule,
            severity=severity,
            min_rank=min_rank,
            limit=limit,
        )
        for alert in alerts:
            _validate_alert(alert)
        return alerts

    @app.get("/alerts/{alert_id}")
    def get_alert(alert_id: str, request: Request) -> dict[str, Any]:
        """Retrieve a single detection alert by alert ID."""
        s: SQLiteAlertStore = request.app.state.alert_store
        alert = s.get_alert(alert_id)
        if alert is None:
            raise HTTPException(status_code=404, detail="Alert not found")
        _validate_alert(alert)
        return alert

    @app.get("/instances/{resource_type}/{resource_id}")
    def get_instance_state(
        resource_type: str,
        resource_id: str,
        request: Request,
    ) -> dict[str, Any]:
        """Query real-time state and taint status of an active business object instance."""
        inst_store: InMemoryInstanceStore = request.app.state.instance_store
        record = inst_store.get(resource_type, resource_id)
        if record is None:
            raise HTTPException(
                status_code=404,
                detail=f"Instance {resource_type}:{resource_id} not found",
            )
        return {
            "resource_type": record.resource_type,
            "resource_id": record.resource_id,
            "current_state": record.current_state,
            "tainted": bool(record.tainted),
            "owner_subject": record.owner_subject,
            "history_length": len(record.history),
        }

    @app.get("/ui", response_class=HTMLResponse)
    def ui_triage(
        request: Request,
        label: str | None = Query(default=None),
        rule: str | None = Query(default=None),
        min_rank: float | None = Query(default=None),
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=50, ge=1, le=1000),
    ) -> Response:
        """Server-side rendered HTML alert triage table with badges, filtering, and XSS safety."""
        s: SQLiteAlertStore = request.app.state.alert_store
        clean_label = label if label and label.strip() else None
        clean_rule = rule if rule and rule.strip() else None

        alerts, total = AlertFilterEngine.apply_filters(
            conn=s._conn,
            label=clean_label,
            rule=clean_rule,
            min_rank=min_rank,
            offset=offset,
            limit=limit,
        )
        return request.app.state.templates.TemplateResponse(
            request=request,
            name="index.html",
            context={
                "alerts": alerts,
                "total": total,
                "selected_label": clean_label,
                "selected_rule": clean_rule,
                "selected_min_rank": min_rank,
            },
        )

    return app


app = create_dashboard_app()
