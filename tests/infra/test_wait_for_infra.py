"""Unit tests for scripts/wait_for_infra.py (sockets and HTTP are mocked)."""

from __future__ import annotations

import importlib.util
import os
import socket
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from unittest import mock

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "wait_for_infra.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("wait_for_infra", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


wfi = _load()


class FakeClock:
    """Deterministic clock whose sleep advances time."""

    def __init__(self) -> None:
        self.now = 0.0

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def test_tcp_check_success() -> None:
    with mock.patch.object(socket, "create_connection") as conn:
        assert wfi.tcp_check("h", 1) is True
        conn.assert_called_once()


def test_tcp_check_failure() -> None:
    with mock.patch.object(socket, "create_connection", side_effect=OSError):
        assert wfi.tcp_check("h", 1) is False


def test_http_check_rejects_non_http_scheme() -> None:
    assert wfi.http_check("file:///etc/passwd") is False


def test_http_check_success_and_failure() -> None:
    resp = mock.MagicMock()
    resp.status = 200
    resp.__enter__.return_value = resp
    with mock.patch.object(wfi.urllib.request, "urlopen", return_value=resp):
        assert wfi.http_check("http://x/") is True
    with mock.patch.object(wfi.urllib.request, "urlopen", side_effect=OSError):
        assert wfi.http_check("http://x/") is False


def test_wait_success_immediately() -> None:
    fc = FakeClock()
    checks = [wfi.Check("a", lambda: True), wfi.Check("b", lambda: True)]
    assert wfi.wait_for_infra(checks, 5, 1, fc.clock, fc.sleep) is True
    assert fc.now == 0


def test_wait_success_after_retries() -> None:
    fc = FakeClock()
    calls = {"n": 0}

    def flaky() -> bool:
        calls["n"] += 1
        return calls["n"] >= 3

    assert wfi.wait_for_infra([wfi.Check("a", flaky)], 10, 1, fc.clock, fc.sleep) is True
    assert fc.now == 2


def test_wait_timeout(capsys: pytest.CaptureFixture[str]) -> None:
    fc = FakeClock()
    checks = [wfi.Check("up", lambda: True), wfi.Check("down", lambda: False)]
    assert wfi.wait_for_infra(checks, 3, 1, fc.clock, fc.sleep) is False
    err = capsys.readouterr().err
    assert "down" in err and "up," not in err


def test_build_checks_env_override() -> None:
    with mock.patch.object(wfi, "tcp_check", return_value=True) as tcp:
        checks = wfi.build_checks({"KAFKA_HOST": "k", "KAFKA_PORT": "1111"})
        checks[0].probe()
        tcp.assert_called_with("k", 1111)
    assert [c.name for c in checks] == ["kafka", "redis", "neo4j-bolt", "neo4j-http"]


def test_main_exit_0_when_ports_available() -> None:
    with (
        mock.patch.object(socket, "create_connection"),
        mock.patch.object(wfi, "http_check", return_value=True),
    ):
        assert wfi.main(["--timeout", "1"]) == 0


def test_main_exit_1_on_timeout() -> None:
    with (
        mock.patch.object(socket, "create_connection", side_effect=OSError),
        mock.patch.object(wfi.time, "sleep"),
        mock.patch.object(wfi.time, "monotonic", side_effect=[0.0, 0.0, 5.0, 5.0, 5.0]),
    ):
        assert wfi.main(["--timeout", "1"]) == 1


def test_script_exits_nonzero_via_cli() -> None:
    env = {
        **os.environ,
        "KAFKA_PORT": "1",
        "REDIS_PORT": "1",
        "NEO4J_BOLT_PORT": "1",
        "NEO4J_HTTP_PORT": "1",
    }
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "--timeout", "1"],
        env=env,
        capture_output=True,
        check=False,
        timeout=30,
    )
    assert r.returncode == 1
