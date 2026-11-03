"""Unit tests for prototype demonstration script using mocked HTTP responses."""

from __future__ import annotations

from typing import Any

import httpx

from scripts.run_prototype_demo import (
    format_table,
    main,
    query_and_print_alerts,
    run_demo,
    verify_services,
)


def create_mock_transport(healthy: bool = True) -> httpx.MockTransport:
    """Create a mock transport simulating Gateway, Target App, and Dashboard API."""
    order_counter = 1000

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal order_counter
        url_path = request.url.path
        method = request.method

        if url_path == "/health":
            if healthy:
                return httpx.Response(200, json={"status": "healthy"})
            return httpx.Response(503, json={"status": "unhealthy"})

        if url_path == "/cart/items" and method == "POST":
            return httpx.Response(200, json={"cart_id": "cart-1"})

        if url_path == "/orders" and method == "POST":
            order_counter += 1
            return httpx.Response(
                201,
                json={"order_id": f"order-{order_counter}", "state": "ORDER_CREATED"},
            )

        if "/address" in url_path and method == "PUT":
            return httpx.Response(200, json={"status": "ok"})

        if "/payment" in url_path and method == "POST":
            return httpx.Response(200, json={"status": "pending"})

        if url_path == "/payments/webhook" and method == "POST":
            return httpx.Response(200, json={"status": "paid"})

        if "/fulfill" in url_path and method == "POST":
            return httpx.Response(200, json={"status": "fulfilled"})

        if "/ship" in url_path and method == "POST":
            return httpx.Response(200, json={"status": "shipped"})

        if "/refund" in url_path and method == "POST":
            return httpx.Response(200, json={"status": "refunded"})

        if url_path == "/alerts" and method == "GET":
            sample_alerts: list[dict[str, Any]] = [
                {
                    "alert_id": "828dd705-e7e9-440a-a1dc-151582c35411",
                    "label": "SKIPPED",
                    "rule": "R2",
                    "owasp_class": "BLA6",
                    "expected_path": [
                        "POST /orders",
                        "PUT /orders/{id}/address",
                        "POST /orders/{id}/payment",
                        "POST /payments/webhook",
                        "POST /orders/{id}/fulfill",
                    ],
                    "observed": {"operation": "POST /orders/{id}/fulfill"},
                    "missing": ["POST /orders/{id}/payment", "POST /payments/webhook"],
                },
                {
                    "alert_id": "a90ee705-e7e9-440a-a1dc-151582c35999",
                    "label": "REPLAYED",
                    "rule": "R1",
                    "owasp_class": "API6:2023 Unrestricted Access to Sensitive Business Flows",
                    "expected_path": ["POST /orders", "POST /orders/{id}/refund"],
                    "observed": {"operation": "POST /orders/{id}/refund"},
                    "missing": [],
                },
            ]
            return httpx.Response(200, json=sample_alerts)

        return httpx.Response(404, json={"detail": "Not found"})

    return httpx.MockTransport(handler)


def test_verify_services_healthy() -> None:
    transport = create_mock_transport(healthy=True)
    with httpx.Client(transport=transport) as client:
        ok = verify_services(
            client=client,
            gateway_url="http://gw:8000",
            dashboard_url="http://dash:8080",
            target_app_url="http://target:8001",
        )
        assert ok is True


def test_verify_services_unhealthy() -> None:
    transport = create_mock_transport(healthy=False)
    with httpx.Client(transport=transport) as client:
        ok = verify_services(
            client=client,
            gateway_url="http://gw:8000",
            dashboard_url="http://dash:8080",
            target_app_url="http://target:8001",
        )
        assert ok is False


def test_format_table() -> None:
    headers = ["COL A", "COL B"]
    rows = [["1", "foo"], ["22", "barrr"]]
    table = format_table(headers, rows)
    assert "COL A" in table
    assert "COL B" in table
    assert "foo" in table
    assert "barrr" in table


def test_query_and_print_alerts(capsys: Any) -> None:
    transport = create_mock_transport(healthy=True)
    with httpx.Client(transport=transport) as client:
        alerts = query_and_print_alerts(client, "http://dash:8080")
        assert len(alerts) == 2
        captured = capsys.readouterr()
        assert "ALERT ID" in captured.out
        assert "VIOLATION TYPE" in captured.out
        assert "SKIPPED" in captured.out
        assert "REPLAYED" in captured.out
        assert "MISSING OPERATIONS" in captured.out


def test_run_demo_success(capsys: Any) -> None:
    transport = create_mock_transport(healthy=True)
    with httpx.Client(transport=transport) as client:
        exit_code = run_demo(
            gateway_url="http://gw:8000",
            dashboard_url="http://dash:8080",
            target_app_url="http://target:8001",
            client=client,
        )
        assert exit_code == 0
        captured = capsys.readouterr()
        assert "1. Verifying Services Health" in captured.out
        assert "2. Replaying Legal Checkout Flow" in captured.out
        assert "3. Executing Attack 1 (Skip Payment)" in captured.out
        assert "4. Executing Attack 3 (Double Refund)" in captured.out
        assert "5. Querying Straph Dashboard Alerts" in captured.out
        assert "[✓] Prototype demo completed successfully" in captured.out


def test_run_demo_service_unhealthy() -> None:
    transport = create_mock_transport(healthy=False)
    with httpx.Client(transport=transport) as client:
        exit_code = run_demo(
            gateway_url="http://gw:8000",
            dashboard_url="http://dash:8080",
            target_app_url="http://target:8001",
            client=client,
        )
        assert exit_code == 1


def test_main_cli(monkeypatch: Any) -> None:
    transport = create_mock_transport(healthy=True)
    original_client = httpx.Client

    def mock_client_factory(*args: Any, **kwargs: Any) -> httpx.Client:
        return original_client(transport=transport)

    monkeypatch.setattr(httpx, "Client", mock_client_factory)

    exit_code = main(
        [
            "--gateway-url",
            "http://gw:8000",
            "--dashboard-url",
            "http://dash:8080",
            "--target-app-url",
            "http://target:8001",
        ]
    )
    assert exit_code == 0
