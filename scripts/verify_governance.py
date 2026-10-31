"""Verify repository governance: CODEOWNERS, commit template and pre-commit hooks."""

from __future__ import annotations

import re
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import yaml

REQUIRED_CODEOWNER_PATHS: tuple[str, ...] = (
    "/contracts/",
    "/services/gateway/",
    "/services/model_builder/",
    "/services/detector/",
    "/services/ranker/",
    "/services/dashboard/",
    "/services/evaluation/",
)
_HANDLE = re.compile(r"^@[A-Za-z0-9][A-Za-z0-9_.-]*(/[A-Za-z0-9_.-]+)?$")
_TEMPLATE_HEADER = re.compile(r"^#\s*<type>\(<scope>\):", re.MULTILINE)
_TRAILER = re.compile(r"^#?\s*Assisted-by:", re.MULTILINE)


def check_codeowners(root: Path) -> list[str]:
    """Return errors for missing or unowned required CODEOWNERS paths."""
    path = root / ".github" / "CODEOWNERS"
    if not path.is_file():
        return [f"missing file: {path.relative_to(root).as_posix()}"]
    owners: dict[str, list[str]] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        pattern, *handles = line.split()
        owners[pattern] = handles
    errors: list[str] = []
    for required in REQUIRED_CODEOWNER_PATHS:
        handles = owners.get(required)
        if handles is None:
            errors.append(f"CODEOWNERS: no entry for {required}")
        elif not handles or not all(_HANDLE.match(h) for h in handles):
            errors.append(f"CODEOWNERS: {required} has no valid team handle")
    return errors


def check_gitmessage(root: Path) -> list[str]:
    """Return errors if the commit template or its Assisted-by trailer is missing."""
    path = root / ".gitmessage"
    if not path.is_file():
        return ["missing file: .gitmessage"]
    text = path.read_text(encoding="utf-8")
    errors: list[str] = []
    if not _TEMPLATE_HEADER.search(text):
        errors.append(".gitmessage: Conventional Commit '<type>(<scope>):' header missing")
    if not _TRAILER.search(text):
        errors.append(".gitmessage: 'Assisted-by:' trailer missing")
    return errors


def _hook_ids_and_entries(config: Any) -> list[tuple[str, str]]:
    hooks: list[tuple[str, str]] = []
    repos = config.get("repos") if isinstance(config, dict) else None
    for repo in repos if isinstance(repos, list) else []:
        if not isinstance(repo, dict):
            continue
        for hook in repo.get("hooks") or []:
            if isinstance(hook, dict):
                hooks.append((str(hook.get("id", "")), str(hook.get("entry", ""))))
    return hooks


def check_precommit(root: Path) -> list[str]:
    """Return errors if ruff lint, ruff format or pytest hooks are not configured."""
    path = root / ".pre-commit-config.yaml"
    if not path.is_file():
        return ["missing file: .pre-commit-config.yaml"]
    try:
        config = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        return [f".pre-commit-config.yaml: invalid YAML ({exc.__class__.__name__})"]
    hooks = _hook_ids_and_entries(config)

    def has(predicate: str) -> bool:
        return any(re.search(predicate, f"{i} {e}") for i, e in hooks)

    checks = {
        "ruff lint": r"\bruff(-check)?\b(?!.*format)",
        "ruff format": r"ruff[- ]format",
        "pytest": r"\bpytest\b",
    }
    errors: list[str] = []
    for label, pattern in checks.items():
        if label == "ruff lint":
            ok = any(
                re.search(r"\bruff\b", f"{i} {e}") and not re.search(r"format", f"{i} {e}")
                for i, e in hooks
            )
        else:
            ok = has(pattern)
        if not ok:
            errors.append(f".pre-commit-config.yaml: {label} hook missing")
    return errors


def verify(root: Path) -> list[str]:
    """Run all governance checks against ``root`` and return all errors."""
    return check_codeowners(root) + check_gitmessage(root) + check_precommit(root)


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point; returns 0 when governance is valid, 1 otherwise."""
    args = list(sys.argv[1:] if argv is None else argv)
    root = Path(args[0]) if args else Path(__file__).resolve().parents[1]
    errors = verify(root)
    for error in errors:
        print(f"FAIL: {error}", file=sys.stderr)
    if not errors:
        print("Governance checks passed.")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
