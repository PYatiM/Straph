"""Structure checks for .github/workflows/ci.yml."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

import pytest
import yaml

WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "ci.yml"


@pytest.fixture(scope="module")
def wf() -> dict[str, Any]:
    data = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def _triggers(wf: dict[str, Any]) -> dict[str, Any]:
    # PyYAML (YAML 1.1) parses the bare key `on` as boolean True.
    return wf.get("on", wf.get(True))


def _job(wf: dict[str, Any]) -> dict[str, Any]:
    return wf["jobs"]["lint-and-test"]


def _runs(wf: dict[str, Any]) -> list[str]:
    return [s["run"] for s in _job(wf)["steps"] if "run" in s]


def test_single_workflow_and_job(wf: dict[str, Any]) -> None:
    assert wf["name"] == "lint-and-test"
    assert list(wf["jobs"]) == ["lint-and-test"]


def test_triggers_target_main(wf: dict[str, Any]) -> None:
    triggers = _triggers(wf)
    assert set(triggers) == {"push", "pull_request"}
    assert triggers["push"]["branches"] == ["main"]
    assert triggers["pull_request"]["branches"] == ["main"]


def test_runner(wf: dict[str, Any]) -> None:
    assert _job(wf)["runs-on"] == "ubuntu-latest"


def test_python_setup_with_cache(wf: dict[str, Any]) -> None:
    step = next(
        s for s in _job(wf)["steps"] if s.get("uses", "").startswith("actions/setup-python")
    )
    assert step["with"]["python-version"] == "3.11"
    assert step["with"]["cache"] == "pip"
    assert step["with"]["cache-dependency-path"] == "pyproject.toml"


def test_installs_from_pyproject(wf: dict[str, Any]) -> None:
    assert any("pip install" in r and ".[dev]" in r for r in _runs(wf))
    project = tomllib.loads((WORKFLOW.parents[2] / "pyproject.toml").read_text())
    assert "ruff" in project["project"]["optional-dependencies"]["dev"]


def test_required_steps_in_order(wf: dict[str, Any]) -> None:
    runs = _runs(wf)
    expected = [
        "ruff check .",
        "ruff format --check .",
        "pytest tests/contracts/ tests/infra/",
    ]
    idx = [runs.index(cmd) for cmd in expected]
    assert idx == sorted(idx)
