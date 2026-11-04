"""Tests validating dashboard and ranker container definitions in docker-compose.yml."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
COMPOSE_PATH = ROOT / "docker-compose.yml"
DASHBOARD_DOCKERFILE = ROOT / "services" / "dashboard" / "Dockerfile"
RANKER_DOCKERFILE = ROOT / "services" / "ranker" / "Dockerfile"


@pytest.fixture(scope="module")
def compose_data() -> dict[str, Any]:
    """Parse docker-compose.yml."""
    data = yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def test_dashboard_service_definition(compose_data: dict[str, Any]) -> None:
    """Validate dashboard service build, ports, volume, and environment."""
    services = compose_data["services"]
    assert "dashboard" in services, "dashboard service missing from docker-compose.yml"

    dashboard = services["dashboard"]
    build = dashboard.get("build", {})
    assert build.get("context") == "."
    assert build.get("dockerfile") == "services/dashboard/Dockerfile"

    assert DASHBOARD_DOCKERFILE.exists()
    dockerfile_text = DASHBOARD_DOCKERFILE.read_text(encoding="utf-8")
    assert "EXPOSE 8080" in dockerfile_text
    assert "services.dashboard.app:app" in dockerfile_text
    assert "USER appuser" in dockerfile_text

    ports = dashboard.get("ports", [])
    assert any("8080:8080" in str(p) for p in ports)
    assert any(str(p).startswith("127.0.0.1:8080:8080") for p in ports)

    volumes = dashboard.get("volumes", [])
    assert any("alerts-data" in str(v) for v in volumes)

    env = dashboard.get("environment", {})
    assert "ALERTS_DB_PATH" in env


def test_ranker_service_definition(compose_data: dict[str, Any]) -> None:
    """Validate ranker service build, dependencies, volume, and environment."""
    services = compose_data["services"]
    assert "ranker" in services, "ranker service missing from docker-compose.yml"

    ranker = services["ranker"]
    build = ranker.get("build", {})
    assert build.get("context") == "."
    assert build.get("dockerfile") == "services/ranker/Dockerfile"

    assert RANKER_DOCKERFILE.exists()
    dockerfile_text = RANKER_DOCKERFILE.read_text(encoding="utf-8")
    assert "services.ranker.service" in dockerfile_text
    assert "USER appuser" in dockerfile_text

    depends_on = ranker.get("depends_on", [])
    if isinstance(depends_on, dict):
        deps = set(depends_on.keys())
    else:
        deps = set(depends_on)
    assert "kafka" in deps

    volumes = ranker.get("volumes", [])
    assert any("alerts-data" in str(v) for v in volumes)

    env = ranker.get("environment", {})
    assert "KAFKA_BOOTSTRAP_SERVERS" in env
    assert "ALERTS_DB_PATH" in env


def test_alerts_data_volume(compose_data: dict[str, Any]) -> None:
    """Validate alerts-data shared volume is declared."""
    volumes = compose_data.get("volumes", {})
    assert "alerts-data" in volumes
