"""Tests for the target app container setup (Dockerfile, Compose) and the /health route."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml

from services.evaluation.target_app.app import create_app
from services.evaluation.target_app.flaws import ENV_BOLA_CANCEL, Flaws

REPO_ROOT = Path(__file__).resolve().parents[2]
TARGET_DIR = REPO_ROOT / "services" / "evaluation" / "target_app"
DOCKERFILE = TARGET_DIR / "Dockerfile"
REQUIREMENTS = TARGET_DIR / "requirements.txt"
COMPOSE_FILE = REPO_ROOT / "docker-compose.yml"

EXPECTED_CMD = [
    "uvicorn",
    "services.evaluation.target_app.app:app",
    "--host",
    "0.0.0.0",
    "--port",
    "8001",
]


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _instructions() -> list[tuple[str, str]]:
    """Dockerfile as (INSTRUCTION, arguments) pairs, joining line continuations."""
    text = DOCKERFILE.read_text(encoding="utf-8").replace("\\\n", " ")
    pairs = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        keyword, _, rest = line.partition(" ")
        pairs.append((keyword.upper(), rest.strip()))
    return pairs


def _args(keyword: str) -> list[str]:
    return [rest for kw, rest in _instructions() if kw == keyword]


@pytest.fixture(scope="module")
def target_service() -> dict[str, Any]:
    compose = yaml.safe_load(COMPOSE_FILE.read_text(encoding="utf-8"))
    service: dict[str, Any] = compose["services"]["target-app"]
    return service


# --- GET /health --------------------------------------------------------------------------


@pytest.mark.anyio
async def test_health_returns_expected_body() -> None:
    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "healthy", "service": "straph-target-app"}


@pytest.mark.anyio
async def test_health_needs_no_auth_headers() -> None:
    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/health", headers={})
    assert resp.status_code == 200


@pytest.mark.anyio
async def test_health_works_with_flaws_enabled() -> None:
    flaws = Flaws(skip_payment=True, bola_cancel=True)
    transport = httpx.ASGITransport(app=create_app(flaws=flaws))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/health")
    assert resp.status_code == 200


def test_health_is_not_part_of_the_business_api_spec() -> None:
    assert "/health" not in create_app().openapi()["paths"]


@pytest.mark.anyio
async def test_health_rejects_post() -> None:
    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/health")
    assert resp.status_code == 405


# --- Dockerfile ---------------------------------------------------------------------------


def test_dockerfile_uses_python_311_slim() -> None:
    assert _args("FROM") == ["python:3.11-slim"]


def test_dockerfile_sets_workdir_app() -> None:
    assert _args("WORKDIR") == ["/app"]


def test_dockerfile_exposes_port_8001() -> None:
    assert _args("EXPOSE") == ["8001"]


def test_dockerfile_runs_uvicorn_on_8001() -> None:
    (cmd,) = _args("CMD")
    assert yaml.safe_load(cmd) == EXPECTED_CMD  # exec-form CMD is a JSON list


def test_dockerfile_installs_requirements_without_pip_cache() -> None:
    runs = [r for r in _args("RUN") if "pip install" in r]
    assert len(runs) == 1
    assert "--no-cache-dir" in runs[0]
    assert "-r requirements.txt" in runs[0]


def test_dockerfile_copies_requirements_before_source_for_layer_caching() -> None:
    steps = _instructions()
    req_copy = next(i for i, (k, v) in enumerate(steps) if k == "COPY" and "requirements" in v)
    pip_run = next(i for i, (k, v) in enumerate(steps) if k == "RUN" and "pip install" in v)
    src_copy = next(
        i
        for i, (k, v) in enumerate(steps)
        if k == "COPY" and v.startswith("services/evaluation/target_app/ ")
    )
    assert req_copy < pip_run < src_copy


def test_dockerfile_does_not_copy_whole_repository() -> None:
    for value in _args("COPY") + _args("ADD"):
        source = value.split()[0]
        assert source not in {".", "./", "*"}, value


def test_dockerfile_runs_as_non_root_user() -> None:
    users = _args("USER")
    assert users
    assert users[-1] not in {"root", "0"}


def test_dockerfile_contains_no_hardcoded_credentials() -> None:
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert not re.search(r"(?i)(password|secret|token|api_key)\s*[=:]", text)
    assert not any(kw in {"ENV", "ARG"} and "ALLOW_" in v for kw, v in _instructions())


def test_requirements_cover_runtime_only() -> None:
    names = {
        re.split(r"[<>=!~\[ ]", line.strip(), maxsplit=1)[0].lower()
        for line in REQUIREMENTS.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    }
    assert {"fastapi", "uvicorn", "pydantic"} <= names
    assert not names & {"pytest", "ruff", "httpx", "fakeredis", "mypy"}


# --- docker-compose.yml -------------------------------------------------------------------


def test_compose_builds_target_app_from_the_dockerfile(target_service: dict[str, Any]) -> None:
    build = target_service["build"]
    context = (COMPOSE_FILE.parent / build["context"]).resolve()
    assert (context / build["dockerfile"]).resolve() == DOCKERFILE.resolve()
    # The Dockerfile copies paths starting at "services/", so the context must be the repo root.
    assert context == REPO_ROOT


def test_compose_publishes_port_8001_on_loopback_only(target_service: dict[str, Any]) -> None:
    ports = [str(p) for p in target_service["ports"]]
    assert ports == ["127.0.0.1:8001:8001"]


def test_compose_healthcheck_probes_health_route(target_service: dict[str, Any]) -> None:
    command = target_service["healthcheck"]["test"]
    assert command[0] in {"CMD", "CMD-SHELL"}
    joined = " ".join(command)
    assert "8001/health" in joined
    assert "curl" not in joined  # python:3.11-slim does not ship curl


def test_compose_healthcheck_has_timing_settings(target_service: dict[str, Any]) -> None:
    check = target_service["healthcheck"]
    for key in ("interval", "timeout", "retries", "start_period"):
        assert key in check, key
    assert int(check["retries"]) >= 1


def test_compose_flaw_flags_default_to_off(target_service: dict[str, Any]) -> None:
    environment = target_service.get("environment", {})
    for name, value in environment.items():
        if name.startswith("ALLOW_"):
            assert re.fullmatch(r"\$\{" + name + r":-false\}", str(value)), name


def test_compose_has_no_hardcoded_secrets(target_service: dict[str, Any]) -> None:
    for name in target_service.get("environment", {}):
        assert not re.search(r"(?i)password|secret|token|key", name), name


def test_flag_names_in_compose_match_the_app(target_service: dict[str, Any]) -> None:
    assert ENV_BOLA_CANCEL in target_service.get("environment", {})
