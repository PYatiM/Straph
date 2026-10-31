"""Structure checks for docker-compose.yml and .env.example."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
COMPOSE_PATH = ROOT / "docker-compose.yml"
SECRET_KEY = re.compile(r"PASSWORD|SECRET|TOKEN|AUTH|KEY", re.IGNORECASE)
INTERPOLATED = re.compile(r"\$\{[A-Z0-9_]+(:\?[^}]*)?\}")


@pytest.fixture(scope="module")
def compose() -> dict[str, Any]:
    data = yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def svc(compose: dict[str, Any], name: str) -> dict[str, Any]:
    return compose["services"][name]


def test_required_services(compose: dict[str, Any]) -> None:
    assert set(compose["services"]) == {"kafka", "redis", "neo4j", "target-app"}


@pytest.mark.parametrize(
    ("name", "image"),
    [
        ("kafka", "confluentinc/cp-kafka:7.5.0"),
        ("redis", "redis:7.2.4-alpine"),
        ("neo4j", "neo4j:5.18.0-community"),
    ],
)
def test_pinned_images(compose: dict[str, Any], name: str, image: str) -> None:
    assert svc(compose, name)["image"] == image


@pytest.mark.parametrize(
    ("name", "ports"),
    [
        ("kafka", {9092}),
        ("redis", {6379}),
        ("neo4j", {7474, 7687}),
        ("target-app", {8001}),
    ],
)
def test_published_ports(compose: dict[str, Any], name: str, ports: set[int]) -> None:
    published = set()
    for mapping in svc(compose, name)["ports"]:
        host, container = str(mapping).rsplit(":", 2)[-2:]
        assert host == container
        assert str(mapping).startswith("127.0.0.1:")
        published.add(int(host))
    assert published == ports


def test_kafka_is_kraft(compose: dict[str, Any]) -> None:
    env = svc(compose, "kafka")["environment"]
    assert env["KAFKA_PROCESS_ROLES"] == "broker,controller"
    assert "KAFKA_CONTROLLER_QUORUM_VOTERS" in env
    assert "CLUSTER_ID" in env
    assert not any("ZOOKEEPER" in key for key in env)
    assert "localhost:9092" in env["KAFKA_ADVERTISED_LISTENERS"]


def test_redis_persistent_volume(compose: dict[str, Any]) -> None:
    redis = svc(compose, "redis")
    assert "yes" in redis["command"]
    assert "redis-data:/data" in redis["volumes"]
    assert "redis-data" in compose["volumes"]


def test_neo4j_auth_from_environment(compose: dict[str, Any]) -> None:
    env = svc(compose, "neo4j")["environment"]
    assert env["NEO4J_AUTH"].startswith("neo4j/${NEO4J_PASSWORD")
    assert "neo4j-data:/data" in svc(compose, "neo4j")["volumes"]


def test_target_app_build_placeholder(compose: dict[str, Any]) -> None:
    context = svc(compose, "target-app")["build"]["context"]
    assert context == "./services/evaluation/target_app"
    assert (ROOT / context / "Dockerfile").is_file()


@pytest.mark.parametrize(
    ("name", "fragments"),
    [
        ("kafka", ["kafka-topics", "--bootstrap-server localhost:9092", "--list"]),
        ("redis", ["redis-cli", "ping"]),
        ("neo4j", ["cypher-shell"]),
    ],
)
def test_healthchecks(compose: dict[str, Any], name: str, fragments: list[str]) -> None:
    health = svc(compose, name)["healthcheck"]
    command = " ".join(health["test"][1:])
    assert health["test"][0] in {"CMD", "CMD-SHELL"}
    for fragment in fragments:
        assert fragment in command
    assert {"interval", "timeout", "retries"} <= set(health)


def test_no_hardcoded_secrets(compose: dict[str, Any]) -> None:
    for name, service in compose["services"].items():
        env = service.get("environment", {})
        for key, value in env.items():
            if SECRET_KEY.search(key):
                assert INTERPOLATED.search(str(value)), f"{name}.{key} is hardcoded"
    # Secret-looking values must be passed as ${VAR}, never as literals.
    assert "neo4j/neo4j" not in COMPOSE_PATH.read_text(encoding="utf-8")


def test_env_example_covers_interpolated_variables() -> None:
    text = COMPOSE_PATH.read_text(encoding="utf-8")
    variables = set(re.findall(r"\$\{([A-Z0-9_]+)", text))
    defined = {
        line.split("=", 1)[0]
        for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
        if "=" in line and not line.startswith("#")
    }
    assert variables == {"KAFKA_CLUSTER_ID", "NEO4J_PASSWORD"}
    assert variables <= defined
