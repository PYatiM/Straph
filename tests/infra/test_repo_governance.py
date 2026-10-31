"""Tests for scripts/verify_governance.py."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "verify_governance.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("verify_governance", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


vg = _load()

CODEOWNERS = "".join(f"{p} @org/team\n" for p in vg.REQUIRED_CODEOWNER_PATHS)
GITMESSAGE = "# <type>(<scope>): <summary>\n#\n# Assisted-by: <tool>\n"
PRECOMMIT = """\
repos:
  - repo: local
    hooks:
      - id: ruff
        name: ruff
        entry: ruff check
        language: system
      - id: ruff-format
        name: ruff-format
        entry: ruff format --check
        language: system
      - id: pytest
        name: pytest
        entry: pytest
        language: system
"""


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / ".github").mkdir()
    (tmp_path / ".github" / "CODEOWNERS").write_text(CODEOWNERS)
    (tmp_path / ".gitmessage").write_text(GITMESSAGE)
    (tmp_path / ".pre-commit-config.yaml").write_text(PRECOMMIT)
    return tmp_path


def test_valid_repo_exit_zero(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert vg.main([str(repo)]) == 0
    assert "passed" in capsys.readouterr().out


def test_real_repo_is_valid() -> None:
    assert vg.verify(ROOT) == []


def test_default_root_used(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["verify_governance.py"])
    assert vg.main() == 0


def test_cli_subprocess(repo: Path) -> None:
    ok = subprocess.run([sys.executable, str(SCRIPT), str(repo)], check=False, capture_output=True)
    assert ok.returncode == 0
    (repo / ".gitmessage").unlink()
    bad = subprocess.run([sys.executable, str(SCRIPT), str(repo)], check=False, capture_output=True)
    assert bad.returncode != 0


@pytest.mark.parametrize("missing", vg.REQUIRED_CODEOWNER_PATHS)
def test_missing_codeowner_path(repo: Path, missing: str) -> None:
    path = repo / ".github" / "CODEOWNERS"
    lines = [ln for ln in CODEOWNERS.splitlines() if not ln.startswith(missing + " ")]
    path.write_text("\n".join(lines) + "\n")
    assert vg.main([str(repo)]) != 0
    assert any(missing in e for e in vg.check_codeowners(repo))


def test_codeowners_missing_file(repo: Path) -> None:
    (repo / ".github" / "CODEOWNERS").unlink()
    assert vg.check_codeowners(repo) == ["missing file: .github/CODEOWNERS"]


def test_codeowners_no_or_invalid_handle(repo: Path) -> None:
    (repo / ".github" / "CODEOWNERS").write_text(
        "# comment\n\n/contracts/\n/services/gateway/ notahandle\n"
        + "".join(f"{p} @a\n" for p in vg.REQUIRED_CODEOWNER_PATHS[2:])
    )
    errors = vg.check_codeowners(repo)
    assert "CODEOWNERS: /contracts/ has no valid team handle" in errors
    assert "CODEOWNERS: /services/gateway/ has no valid team handle" in errors


def test_gitmessage_missing_file(repo: Path) -> None:
    (repo / ".gitmessage").unlink()
    assert vg.check_gitmessage(repo) == ["missing file: .gitmessage"]


def test_gitmessage_missing_trailer(repo: Path) -> None:
    (repo / ".gitmessage").write_text("# <type>(<scope>): x\n")
    assert vg.check_gitmessage(repo) == [".gitmessage: 'Assisted-by:' trailer missing"]


def test_gitmessage_missing_header(repo: Path) -> None:
    (repo / ".gitmessage").write_text("Assisted-by: x\n")
    assert len(vg.check_gitmessage(repo)) == 1
    assert "header missing" in vg.check_gitmessage(repo)[0]


def test_precommit_missing_file(repo: Path) -> None:
    (repo / ".pre-commit-config.yaml").unlink()
    assert vg.check_precommit(repo) == ["missing file: .pre-commit-config.yaml"]


def test_precommit_invalid_yaml(repo: Path) -> None:
    (repo / ".pre-commit-config.yaml").write_text("repos: [unclosed")
    assert "invalid YAML" in vg.check_precommit(repo)[0]


@pytest.mark.parametrize(
    ("hook_id", "label"),
    [("ruff", "ruff lint"), ("ruff-format", "ruff format"), ("pytest", "pytest")],
)
def test_precommit_missing_hook(repo: Path, hook_id: str, label: str) -> None:
    block = {
        "ruff": "      - id: ruff\n        entry: ruff check\n",
        "ruff-format": "      - id: ruff-format\n        entry: ruff format --check\n",
        "pytest": "      - id: pytest\n        entry: pytest\n",
    }
    text = "repos:\n  - repo: local\n    hooks:\n" + "".join(
        v for k, v in block.items() if k != hook_id
    )
    (repo / ".pre-commit-config.yaml").write_text(text)
    assert vg.check_precommit(repo) == [f".pre-commit-config.yaml: {label} hook missing"]
    assert vg.main([str(repo)]) != 0


@pytest.mark.parametrize(
    "content", ["", "repos: notalist", "repos:\n  - bad\n  - repo: x\n    hooks:\n      - bad"]
)
def test_precommit_malformed_structure(repo: Path, content: str) -> None:
    (repo / ".pre-commit-config.yaml").write_text(content)
    assert len(vg.check_precommit(repo)) == 3
