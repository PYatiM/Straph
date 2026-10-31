"""Wait until Kafka, Redis and Neo4j respond, or exit 1 after a timeout."""

from __future__ import annotations

import argparse
import os
import socket
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass

DEFAULT_TIMEOUT = 60.0
POLL_INTERVAL = 1.0
CONNECT_TIMEOUT = 2.0


@dataclass(frozen=True)
class Check:
    """A named readiness probe."""

    name: str
    probe: Callable[[], bool]


def tcp_check(host: str, port: int, timeout: float = CONNECT_TIMEOUT) -> bool:
    """Return True if a TCP connection to host:port succeeds."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def http_check(url: str, timeout: float = CONNECT_TIMEOUT) -> bool:
    """Return True if an HTTP GET to url returns a 2xx status."""
    if not url.startswith(("http://", "https://")):
        return False
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return 200 <= int(resp.status) < 300
    except (urllib.error.URLError, OSError, ValueError):
        return False


def build_checks(env: dict[str, str] | None = None) -> list[Check]:
    """Build the probe list; hosts and ports are overridable via environment."""
    e = os.environ if env is None else env
    kafka = (e.get("KAFKA_HOST", "127.0.0.1"), int(e.get("KAFKA_PORT", "9092")))
    redis = (e.get("REDIS_HOST", "127.0.0.1"), int(e.get("REDIS_PORT", "6379")))
    neo_host = e.get("NEO4J_HOST", "127.0.0.1")
    bolt = (neo_host, int(e.get("NEO4J_BOLT_PORT", "7687")))
    http_url = f"http://{neo_host}:{int(e.get('NEO4J_HTTP_PORT', '7474'))}/"
    return [
        Check("kafka", lambda: tcp_check(*kafka)),
        Check("redis", lambda: tcp_check(*redis)),
        Check("neo4j-bolt", lambda: tcp_check(*bolt)),
        Check("neo4j-http", lambda: http_check(http_url)),
    ]


def wait_for_infra(
    checks: Sequence[Check],
    timeout: float = DEFAULT_TIMEOUT,
    interval: float = POLL_INTERVAL,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> bool:
    """Poll until every check passes (True) or the timeout elapses (False)."""
    deadline = clock() + timeout
    pending = list(checks)
    while True:
        pending = [c for c in pending if not c.probe()]
        if not pending:
            return True
        if clock() >= deadline:
            names = ", ".join(c.name for c in pending)
            print(f"timed out waiting for: {names}", file=sys.stderr)
            return False
        sleep(interval)


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point; returns 0 when ready, 1 on timeout."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--timeout",
        type=float,
        default=float(os.environ.get("INFRA_WAIT_TIMEOUT", DEFAULT_TIMEOUT)),
        help="seconds to wait (default: 60, or INFRA_WAIT_TIMEOUT)",
    )
    args = parser.parse_args(argv)
    if wait_for_infra(build_checks(), timeout=args.timeout):
        print("infrastructure ready")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
