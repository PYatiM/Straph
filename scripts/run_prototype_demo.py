#!/usr/bin/env python3
"""Week 2 Prototype demonstration script for Straph IDS.

Verifies service health, replays legal checkout and attack traffic (Attack 1: Skip Payment,
Attack 3: Double Refund), queries the Dashboard API /alerts, and pretty-prints the results.
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Any

import httpx

# ANSI color codes
COLOR_RESET = "\033[0m"
COLOR_GREEN = "\033[92m"
COLOR_YELLOW = "\033[93m"
COLOR_RED = "\033[91m"
COLOR_CYAN = "\033[96m"
COLOR_BOLD = "\033[1m"


def print_status(message: str, color: str = COLOR_GREEN) -> None:
    """Print a colored status line."""
    print(f"{color}{message}{COLOR_RESET}")


def verify_services(
    client: httpx.Client,
    gateway_url: str,
    dashboard_url: str,
    target_app_url: str,
) -> bool:
    """Verify all required services respond with healthy status."""
    services = [
        ("Gateway", f"{gateway_url}/health"),
        ("Dashboard", f"{dashboard_url}/health"),
        ("Target App", f"{target_app_url}/health"),
    ]
    all_ok = True
    for name, url in services:
        try:
            resp = client.get(url)
            if resp.status_code == 200:
                print_status(f"  [✓] {name} is healthy ({url})", COLOR_GREEN)
            else:
                print_status(
                    f"  [✗] {name} returned status {resp.status_code} ({url})",
                    COLOR_RED,
                )
                all_ok = False
        except (httpx.HTTPError, OSError) as exc:
            print_status(f"  [✗] {name} unreachable at {url}: {exc}", COLOR_RED)
            all_ok = False
    return all_ok


def execute_legal_checkout(client: httpx.Client, gateway_url: str) -> str:
    """Send live HTTP traffic for a complete legal checkout flow."""
    headers_cust = {"X-User-Sub": "customer-demo-1"}
    headers_pg = {"X-User-Sub": "pg-service", "X-User-Roles": "payment-gateway"}
    headers_wh = {"X-User-Sub": "wh-worker", "X-User-Roles": "warehouse"}

    # 1. Cart
    r = client.post(
        f"{gateway_url}/cart/items",
        json={"sku": "BOOK-DEMO", "quantity": 1},
        headers=headers_cust,
    )
    r.raise_for_status()

    # 2. Order
    r = client.post(f"{gateway_url}/orders", headers=headers_cust)
    r.raise_for_status()
    order_id: str = r.json().get("order_id", "demo-order-1")

    # 3. Address
    r = client.put(
        f"{gateway_url}/orders/{order_id}/address",
        json={"address": "100 Market St, San Francisco, CA"},
        headers=headers_cust,
    )
    r.raise_for_status()

    # 4. Payment
    r = client.post(f"{gateway_url}/orders/{order_id}/payment", headers=headers_cust)
    r.raise_for_status()

    # 5. Webhook
    r = client.post(
        f"{gateway_url}/payments/webhook",
        json={"order_id": order_id},
        headers=headers_pg,
    )
    r.raise_for_status()

    # 6. Fulfill
    r = client.post(f"{gateway_url}/orders/{order_id}/fulfill", headers=headers_wh)
    r.raise_for_status()

    # 7. Ship
    r = client.post(f"{gateway_url}/orders/{order_id}/ship", headers=headers_wh)
    r.raise_for_status()

    print_status(
        f"  [✓] Legal Checkout Order {order_id} reached SHIPPED state (0 alerts expected)",
        COLOR_GREEN,
    )
    return order_id


def execute_attack_1(client: httpx.Client, gateway_url: str) -> str:
    """Execute Attack 1: Skip Payment (Order -> Address -> Fulfill directly)."""
    headers_cust = {"X-User-Sub": "attacker-1"}
    headers_wh = {"X-User-Sub": "wh-worker", "X-User-Roles": "warehouse"}

    # 1. Cart & Order
    client.post(
        f"{gateway_url}/cart/items",
        json={"sku": "HIGH-VAL-ITEM", "quantity": 1},
        headers=headers_cust,
    )
    r = client.post(f"{gateway_url}/orders", headers=headers_cust)
    r.raise_for_status()
    order_id: str = r.json().get("order_id", "attack1-order")

    # 2. Address
    client.put(
        f"{gateway_url}/orders/{order_id}/address",
        json={"address": "999 Rogue Way"},
        headers=headers_cust,
    )

    # 3. Fulfill directly (skipping payment initiation & webhook confirmation)
    r = client.post(f"{gateway_url}/orders/{order_id}/fulfill", headers=headers_wh)
    print_status(
        f"  [!] Attack 1 executed: Order {order_id} skipped payment -> fulfill returned HTTP {r.status_code}",
        COLOR_YELLOW,
    )
    return order_id


def execute_attack_3(client: httpx.Client, gateway_url: str) -> str:
    """Execute Attack 3: Double Refund (initiates second refund on refunded order)."""
    headers_cust = {"X-User-Sub": "attacker-3"}
    headers_pg = {"X-User-Sub": "pg-service", "X-User-Roles": "payment-gateway"}
    headers_sup = {"X-User-Sub": "support-agent", "X-User-Roles": "support"}

    # 1. Cart, Order, Address, Payment, Webhook (to reach PAID state)
    client.post(
        f"{gateway_url}/cart/items",
        json={"sku": "REFUND-ITEM", "quantity": 1},
        headers=headers_cust,
    )
    r = client.post(f"{gateway_url}/orders", headers=headers_cust)
    r.raise_for_status()
    order_id: str = r.json().get("order_id", "attack3-order")

    client.put(
        f"{gateway_url}/orders/{order_id}/address",
        json={"address": "555 Refund Lane"},
        headers=headers_cust,
    )
    client.post(f"{gateway_url}/orders/{order_id}/payment", headers=headers_cust)
    client.post(
        f"{gateway_url}/payments/webhook",
        json={"order_id": order_id},
        headers=headers_pg,
    )

    # 2. First refund (legal advance to terminal REFUNDED state)
    r1 = client.post(f"{gateway_url}/orders/{order_id}/refund", headers=headers_sup)
    print_status(
        f"  [✓] Order {order_id} refunded legally (HTTP {r1.status_code})",
        COLOR_GREEN,
    )

    # 3. Second refund (exploit: replaying single-use terminal refund transition)
    r2 = client.post(f"{gateway_url}/orders/{order_id}/refund", headers=headers_sup)
    print_status(
        f"  [!] Attack 3 executed: Order {order_id} double refunded (HTTP {r2.status_code})",
        COLOR_YELLOW,
    )
    return order_id


def format_table(headers: list[str], rows: list[list[str]]) -> str:
    """Format headers and rows into a pretty ASCII table."""
    widths = [len(h) for h in headers]
    for row in rows:
        for idx, val in enumerate(row):
            widths[idx] = max(widths[idx], len(val))

    sep = "+" + "+".join("-" * (w + 2) for w in widths) + "+"

    lines: list[str] = [sep]
    header_line = (
        "|" + "|".join(f" {headers[i].ljust(widths[i])} " for i in range(len(headers))) + "|"
    )
    lines.append(header_line)
    lines.append(sep)

    for row in rows:
        row_line = "|" + "|".join(f" {row[i].ljust(widths[i])} " for i in range(len(row))) + "|"
        lines.append(row_line)

    lines.append(sep)
    return "\n".join(lines)


def query_and_print_alerts(
    client: httpx.Client,
    dashboard_url: str,
) -> list[dict[str, Any]]:
    """Query Dashboard API /alerts and display formatted table of detected attacks."""
    resp = client.get(f"{dashboard_url}/alerts")
    resp.raise_for_status()
    alerts: list[dict[str, Any]] = resp.json()

    headers = [
        "ALERT ID",
        "VIOLATION TYPE",
        "RULE",
        "OWASP CLASS",
        "EXPECTED PATH",
        "OBSERVED OPERATION",
        "MISSING OPERATIONS",
    ]

    rows: list[list[str]] = []
    for alert in alerts:
        alert_id = str(alert.get("alert_id", ""))
        label = str(alert.get("label", ""))
        rule = str(alert.get("rule", ""))
        owasp_class = str(alert.get("owasp_class", ""))

        expected = alert.get("expected_path", [])
        expected_str = " -> ".join(expected) if isinstance(expected, list) else str(expected)

        observed = alert.get("observed", {})
        observed_op = observed.get("operation", "") if isinstance(observed, dict) else str(observed)

        missing = alert.get("missing", [])
        missing_str = str(missing) if missing else "[]"

        rows.append(
            [
                alert_id,
                label,
                rule,
                owasp_class,
                expected_str,
                observed_op,
                missing_str,
            ]
        )

    print_status(
        f"\n{COLOR_BOLD}=== STRAPH IDS SECURITY ALERTS DASHBOARD ==={COLOR_RESET}", COLOR_CYAN
    )
    if not rows:
        print("No alerts recorded.")
    else:
        table_text = format_table(headers, rows)
        print(table_text)

    return alerts


def run_demo(
    gateway_url: str,
    dashboard_url: str,
    target_app_url: str,
    client: httpx.Client | None = None,
) -> int:
    """Run full Week 2 prototype demonstration workflow."""
    own_client = False
    if client is None:
        client = httpx.Client(timeout=10.0)
        own_client = True

    try:
        print_status(f"\n{COLOR_BOLD}1. Verifying Services Health...{COLOR_RESET}", COLOR_CYAN)
        if not verify_services(client, gateway_url, dashboard_url, target_app_url):
            print_status("Service health check failed. Ensure services are running.", COLOR_RED)
            return 1

        print_status(f"\n{COLOR_BOLD}2. Replaying Legal Checkout Flow...{COLOR_RESET}", COLOR_CYAN)
        execute_legal_checkout(client, gateway_url)

        print_status(
            f"\n{COLOR_BOLD}3. Executing Attack 1 (Skip Payment)...{COLOR_RESET}", COLOR_CYAN
        )
        execute_attack_1(client, gateway_url)

        print_status(
            f"\n{COLOR_BOLD}4. Executing Attack 3 (Double Refund)...{COLOR_RESET}", COLOR_CYAN
        )
        execute_attack_3(client, gateway_url)

        print_status(
            f"\n{COLOR_BOLD}5. Querying Straph Dashboard Alerts...{COLOR_RESET}", COLOR_CYAN
        )
        alerts = query_and_print_alerts(client, dashboard_url)

        print_status(
            f"\n{COLOR_BOLD}[✓] Prototype demo completed successfully with {len(alerts)} alerts.{COLOR_RESET}",
            COLOR_GREEN,
        )
        return 0
    finally:
        if own_client:
            client.close()


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint parsing arguments and running demonstration."""
    parser = argparse.ArgumentParser(description="Run Straph Prototype Week 2 demonstration.")
    parser.add_argument(
        "--gateway-url",
        default=os.getenv("GATEWAY_URL", "http://localhost:8000"),
        help="Gateway base URL (default: http://localhost:8000)",
    )
    parser.add_argument(
        "--dashboard-url",
        default=os.getenv("DASHBOARD_URL", "http://localhost:8080"),
        help="Dashboard API base URL (default: http://localhost:8080)",
    )
    parser.add_argument(
        "--target-app-url",
        default=os.getenv("TARGET_APP_URL", "http://localhost:8001"),
        help="Target app base URL (default: http://localhost:8001)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        help="HTTP request timeout in seconds (default: 10.0)",
    )
    args = parser.parse_args(argv)

    with httpx.Client(timeout=args.timeout) as client:
        return run_demo(
            gateway_url=args.gateway_url,
            dashboard_url=args.dashboard_url,
            target_app_url=args.target_app_url,
            client=client,
        )


if __name__ == "__main__":
    sys.exit(main())
