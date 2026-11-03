"""Tests asserting Gateway container definition, networking, and dependencies in docker-compose.yml."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
COMPOSE_PATH = ROOT / "docker-compose.yml"
DOCKERFILE_PATH = ROOT / "services" / "gateway" / "Dockerfile"


@pytest.fixture(scope="module")
def compose_data() -> dict[str, Any]:
    data = yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def test_gateway_service_exists(compose_data: dict[str, Any]) -> None:
    """Verify gateway service is defined in docker-compose.yml."""
    assert "gateway" in compose_data["services"]


def test_gateway_build_and_dockerfile(compose_data: dict[str, Any]) -> None:
    """Verify build context, dockerfile path, and Dockerfile contents."""
    gateway = compose_data["services"]["gateway"]
    assert "build" in gateway
    build = gateway["build"]
    assert build["context"] == "."
    assert build["dockerfile"] == "services/gateway/Dockerfile"

    assert DOCKERFILE_PATH.exists()
    dockerfile_content = DOCKERFILE_PATH.read_text(encoding="utf-8")
    assert "FROM python:3.11-slim" in dockerfile_content
    assert "EXPOSE 8000" in dockerfile_content
    assert "uvicorn" in dockerfile_content
    assert "services.gateway.app:app" in dockerfile_content


def test_gateway_ports(compose_data: dict[str, Any]) -> None:
    """Verify gateway exposes port 8000 bound to loopback."""
    gateway = compose_data["services"]["gateway"]
    ports = gateway.get("ports", [])
    assert any("8000:8000" in str(p) for p in ports)
    assert any(str(p).startswith("127.0.0.1:8000:8000") for p in ports)


def test_gateway_dependencies(compose_data: dict[str, Any]) -> None:
    """Verify gateway depends on kafka, redis, and target-app."""
    gateway = compose_data["services"]["gateway"]
    depends_on = gateway.get("depends_on", [])
    if isinstance(depends_on, dict):
        dep_services = set(depends_on.keys())
    else:
        dep_services = set(depends_on)
    assert {"kafka", "redis", "target-app"}.issubset(dep_services)


def test_gateway_environment_mappings(compose_data: dict[str, Any]) -> None:
    """Verify gateway networking environment variables point to internal compose hostnames."""
    gateway = compose_data["services"]["gateway"]
    env = gateway.get("environment", {})
    assert env.get("TARGET_APP_URL") == "http://target-app:8001"
    assert env.get("KAFKA_BOOTSTRAP_SERVERS") == "kafka:9092"
    assert env.get("REDIS_URL") == "redis://redis:6379/0"
