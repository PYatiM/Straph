"""Unit tests for asynchronous live HTTP traffic driver (Prompt P104)."""

from __future__ import annotations

import httpx
import pytest

from scripts.live_traffic_driver import (
    build_arg_parser,
    drive_live_traffic,
    run_user_alpha_legal_checkout,
    run_user_bravo_skip_payment_attack,
)
from services.evaluation.target_app.app import create_app as create_target_app
from services.evaluation.target_app.flaws import Flaws
from services.gateway.app import create_gateway_app
from services.gateway.producer import InMemoryEventProducer


@pytest.mark.anyio
async def test_live_driver_through_gateway_to_target_app() -> None:
    """Drive User Alpha (legal checkout) and User Bravo (skip payment) through Gateway -> Target App."""
    flaws = Flaws(skip_payment=True)
    target_app = create_target_app(strict=False, flaws=flaws)
    target_transport = httpx.ASGITransport(app=target_app)
    downstream_client = httpx.AsyncClient(
        transport=target_transport,
        base_url="http://localhost:8001",
    )

    producer = InMemoryEventProducer()
    gw_app = create_gateway_app(
        target_app_url="http://localhost:8001",
        producer=producer,
        client=downstream_client,
    )
    gw_transport = httpx.ASGITransport(app=gw_app)
    gw_client = httpx.AsyncClient(
        transport=gw_transport,
        base_url="http://localhost:8000",
    )

    async with gw_client, downstream_client:
        results = await drive_live_traffic(
            target_url="http://localhost:8000",
            concurrency=1,
            scenario="all",
            client=gw_client,
        )

    assert len(results) == 2
    alpha_res, bravo_res = results

    # User Alpha: 7 steps (cart, create 201, address, payment, webhook, fulfill, ship)
    assert alpha_res.succeeded is True
    assert alpha_res.error is None
    assert alpha_res.user_sub == "user-alpha"
    assert len(alpha_res.steps) == 7
    assert [s.status_code for s in alpha_res.steps] == [200, 201, 200, 200, 200, 200, 200]
    assert alpha_res.steps[-1].response_body.get("state") == "SHIPPED"

    # User Bravo: 4 steps (cart, create 201, address, fulfill skip payment 200)
    assert bravo_res.succeeded is True
    assert bravo_res.error is None
    assert bravo_res.user_sub == "user-bravo"
    assert len(bravo_res.steps) == 4
    assert [s.status_code for s in bravo_res.steps] == [200, 201, 200, 200]
    assert bravo_res.steps[-1].path.endswith("/fulfill")
    assert bravo_res.steps[-1].response_body.get("state") == "FULFILLMENT_QUEUED"

    # Gateway producer intercepted all 11 events
    assert len(producer.events) == 11


@pytest.mark.anyio
async def test_live_driver_error_handling_on_http_failure() -> None:
    """Verify driver gracefully captures HTTP status errors and network errors."""

    def _failing_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"detail": "internal server error"})

    mock_transport = httpx.MockTransport(_failing_handler)
    async with httpx.AsyncClient(
        transport=mock_transport, base_url="http://localhost:8000"
    ) as client:
        alpha_res = await run_user_alpha_legal_checkout(client, preferred_order_id="3001")
        bravo_res = await run_user_bravo_skip_payment_attack(client, preferred_order_id="3002")

    assert alpha_res.succeeded is False
    assert alpha_res.error is not None
    assert "500" in alpha_res.error

    assert bravo_res.succeeded is False
    assert bravo_res.error is not None
    assert "500" in bravo_res.error


@pytest.mark.anyio
async def test_live_driver_validation_and_cli_flags() -> None:
    """Verify CLI parser flags (--target-url, --concurrency, --scenario) and input validation."""
    parser = build_arg_parser()
    args = parser.parse_args(
        ["--target-url", "http://localhost:9000", "--concurrency", "4", "--scenario", "bravo"]
    )
    assert args.target_url == "http://localhost:9000"
    assert args.concurrency == 4
    assert args.scenario == "bravo"

    with pytest.raises(ValueError, match="concurrency must be >= 1"):
        await drive_live_traffic(concurrency=0)

    with pytest.raises(ValueError, match="Unsupported scenario"):
        await drive_live_traffic(scenario="unknown_scenario")
