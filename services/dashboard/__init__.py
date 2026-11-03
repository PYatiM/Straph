"""Dashboard service package exposing alert storage and REST query API."""

from __future__ import annotations

from services.dashboard.alert_store import SQLiteAlertStore
from services.dashboard.app import create_dashboard_app

__all__ = [
    "SQLiteAlertStore",
    "create_dashboard_app",
]
