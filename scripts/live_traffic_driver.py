#!/usr/bin/env python3
"""Asynchronous live HTTP traffic driver testing Gateway -> Target App pipeline (Prompt P104).

Drives live HTTP requests via httpx.AsyncClient through Gateway (http://localhost:8000)
hitting Target App (http://localhost:8001):
- User Alpha creates order (e.g. 3001), sets address, initiates payment, triggers
  webhook, fulfills, and ships.
- User Bravo creates order (e.g. 3002), sets address, and immediately calls /fulfill
  (Attack 1: skip payment).
Supports CLI flags --target-url, --concurrency, and --scenario.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from dataclasses import dataclass, field
from typing import Any

import httpx

log = logging.getLogger("straph.scripts.live_traffic_driver")

DEFAULT_GATEWAY_URL = "http://localhost:8000"
VALID_SCENARIOS = ("all", "alpha", "bravo", "legal", "skip_payment")


@dataclass(slots=True)
class StepResult:
    """HTTP step outcome captured during a live traffic scenario."""

    step_name: str
    method: str
    path: str
    status_code: int
    response_body: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class WorkflowRunResult:
    """Aggregated outcome of executing a single user workflow scenario."""

    scenario: str
    user_sub: str
    order_id: str
    steps: list[StepResult] = field(default_factory=list)
    succeeded: bool = True
    error: str | None = None


async def _send_step(
    client: httpx.AsyncClient,
    step_name: str,
    method: str,
    path: str,
    *,
    headers: dict[str, str],
    json_body: dict[str, Any] | None = None,
    expected_statuses: tuple[int, ...] = (200, 201),
) -> StepResult:
    """Execute one HTTP request through the Gateway and verify its HTTP status code."""
    resp = await client.request(
        method=method,
        url=path,
        headers=headers,
        json=json_body,
    )
    if resp.status_code not in expected_statuses:
        raise httpx.HTTPStatusError(
            f"Step '{step_name}' ({method} {path}) returned unexpected HTTP {resp.status_code}, "
            f"expected one of {expected_statuses}",
            request=resp.request,
            response=resp,
        )
    try:
        payload = resp.json()
        body = payload if isinstance(payload, dict) else {"data": payload}
    except ValueError:
        body = {}

    return StepResult(
        step_name=step_name,
        method=method,
        path=path,
        status_code=resp.status_code,
        response_body=body,
    )


async def run_user_alpha_legal_checkout(
    client: httpx.AsyncClient,
    *,
    preferred_order_id: str = "3001",
    user_sub: str = "user-alpha",
) -> WorkflowRunResult:
    """Drive User Alpha sequential legal checkout workflow (Cart -> Order -> Address -> Payment -> Webhook -> Fulfill -> Ship)."""
    headers_alpha = {"X-User-Sub": user_sub, "X-User-Roles": "customer"}
    headers_pg = {"X-User-Sub": "pg-service", "X-User-Roles": "payment-gateway"}
    headers_wh = {"X-User-Sub": "wh-worker", "X-User-Roles": "warehouse"}

    result = WorkflowRunResult(
        scenario="alpha_legal_checkout",
        user_sub=user_sub,
        order_id=preferred_order_id,
    )

    try:
        # 1. Add item to cart
        s1 = await _send_step(
            client,
            "add_cart_item",
            "POST",
            "/cart/items",
            headers=headers_alpha,
            json_body={"sku": f"SKU-{preferred_order_id}", "quantity": 1},
            expected_statuses=(200,),
        )
        result.steps.append(s1)

        # 2. Create order
        s2 = await _send_step(
            client,
            "create_order",
            "POST",
            "/orders",
            headers=headers_alpha,
            expected_statuses=(201,),
        )
        result.steps.append(s2)
        order_id = str(s2.response_body.get("order_id") or preferred_order_id)
        result.order_id = order_id

        # 3. Set shipping address
        s3 = await _send_step(
            client,
            "set_address",
            "PUT",
            f"/orders/{order_id}/address",
            headers=headers_alpha,
            json_body={"address": "3001 Alpha Blvd, Suite 100"},
            expected_statuses=(200,),
        )
        result.steps.append(s3)

        # 4. Initiate payment
        s4 = await _send_step(
            client,
            "initiate_payment",
            "POST",
            f"/orders/{order_id}/payment",
            headers=headers_alpha,
            expected_statuses=(200,),
        )
        result.steps.append(s4)

        # 5. Trigger payment webhook
        s5 = await _send_step(
            client,
            "payment_webhook",
            "POST",
            "/payments/webhook",
            headers=headers_pg,
            json_body={"order_id": order_id},
            expected_statuses=(200,),
        )
        result.steps.append(s5)

        # 6. Fulfill order
        s6 = await _send_step(
            client,
            "fulfill_order",
            "POST",
            f"/orders/{order_id}/fulfill",
            headers=headers_wh,
            expected_statuses=(200,),
        )
        result.steps.append(s6)

        # 7. Ship order
        s7 = await _send_step(
            client,
            "ship_order",
            "POST",
            f"/orders/{order_id}/ship",
            headers=headers_wh,
            expected_statuses=(200,),
        )
        result.steps.append(s7)

    except (httpx.HTTPError, OSError) as exc:
        result.succeeded = False
        result.error = str(exc)

    return result


async def run_user_bravo_skip_payment_attack(
    client: httpx.AsyncClient,
    *,
    preferred_order_id: str = "3002",
    user_sub: str = "user-bravo",
) -> WorkflowRunResult:
    """Drive User Bravo Attack 1 (skip_payment): creates order, sets address, immediately calls /fulfill."""
    headers_bravo = {"X-User-Sub": user_sub, "X-User-Roles": "customer"}
    headers_wh = {"X-User-Sub": "wh-worker", "X-User-Roles": "warehouse"}

    result = WorkflowRunResult(
        scenario="bravo_skip_payment_attack",
        user_sub=user_sub,
        order_id=preferred_order_id,
    )

    try:
        # 1. Add item to cart
        s1 = await _send_step(
            client,
            "add_cart_item",
            "POST",
            "/cart/items",
            headers=headers_bravo,
            json_body={"sku": f"SKU-{preferred_order_id}", "quantity": 1},
            expected_statuses=(200,),
        )
        result.steps.append(s1)

        # 2. Create order
        s2 = await _send_step(
            client,
            "create_order",
            "POST",
            "/orders",
            headers=headers_bravo,
            expected_statuses=(201,),
        )
        result.steps.append(s2)
        order_id = str(s2.response_body.get("order_id") or preferred_order_id)
        result.order_id = order_id

        # 3. Set shipping address
        s3 = await _send_step(
            client,
            "set_address",
            "PUT",
            f"/orders/{order_id}/address",
            headers=headers_bravo,
            json_body={"address": "3002 Bravo Lane"},
            expected_statuses=(200,),
        )
        result.steps.append(s3)

        # 4. Immediately call /fulfill (Attack 1: skips payment & webhook)
        # Accepted (200) when ALLOW_SKIP_PAYMENT flaw is active, or rejected (409) in strict mode
        s4 = await _send_step(
            client,
            "fulfill_skip_payment",
            "POST",
            f"/orders/{order_id}/fulfill",
            headers=headers_wh,
            expected_statuses=(200, 409),
        )
        result.steps.append(s4)

    except (httpx.HTTPError, OSError) as exc:
        result.succeeded = False
        result.error = str(exc)

    return result


async def drive_live_traffic(
    *,
    target_url: str = DEFAULT_GATEWAY_URL,
    concurrency: int = 1,
    scenario: str = "all",
    client: httpx.AsyncClient | None = None,
) -> list[WorkflowRunResult]:
    """Execute configured traffic scenarios against the Gateway using httpx.AsyncClient.

    Args:
        target_url: Gateway base URL (default: http://localhost:8000).
        concurrency: Number of concurrent workflow batches to drive (>= 1).
        scenario: Which scenario to execute ('all', 'alpha', 'bravo', 'legal', 'skip_payment').
        client: Optional pre-configured httpx.AsyncClient (used for in-memory ASGI testing).

    Returns:
        List of WorkflowRunResult objects.
    """
    if concurrency < 1:
        raise ValueError("concurrency must be >= 1")
    norm_scenario = scenario.strip().lower()
    if norm_scenario not in VALID_SCENARIOS:
        raise ValueError(f"Unsupported scenario '{scenario}'. Choose from {VALID_SCENARIOS}.")

    own_client = False
    if client is None:
        client = httpx.AsyncClient(base_url=target_url, timeout=10.0)
        own_client = True

    semaphore = asyncio.Semaphore(concurrency)

    async def _run_batch(batch_idx: int) -> list[WorkflowRunResult]:
        async with semaphore:
            batch_results: list[WorkflowRunResult] = []
            alpha_order = str(3001 + batch_idx * 10)
            bravo_order = str(3002 + batch_idx * 10)
            alpha_sub = "user-alpha" if batch_idx == 0 else f"user-alpha-{batch_idx}"
            bravo_sub = "user-bravo" if batch_idx == 0 else f"user-bravo-{batch_idx}"

            if norm_scenario in ("all", "alpha", "legal"):
                res_alpha = await run_user_alpha_legal_checkout(
                    client,
                    preferred_order_id=alpha_order,
                    user_sub=alpha_sub,
                )
                batch_results.append(res_alpha)

            if norm_scenario in ("all", "bravo", "skip_payment"):
                res_bravo = await run_user_bravo_skip_payment_attack(
                    client,
                    preferred_order_id=bravo_order,
                    user_sub=bravo_sub,
                )
                batch_results.append(res_bravo)

            return batch_results

    try:
        batches = await asyncio.gather(*(_run_batch(i) for i in range(concurrency)))
        all_results: list[WorkflowRunResult] = [item for batch in batches for item in batch]
        return all_results
    finally:
        if own_client:
            await client.aclose()


def build_arg_parser() -> argparse.ArgumentParser:
    """Construct CLI argument parser supporting --target-url, --concurrency, and --scenario."""
    parser = argparse.ArgumentParser(
        description="Asynchronous live HTTP traffic driver for Straph Gateway -> Target App."
    )
    parser.add_argument(
        "--target-url",
        default=DEFAULT_GATEWAY_URL,
        help=f"Gateway target base URL (default: {DEFAULT_GATEWAY_URL})",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=1,
        help="Number of concurrent workflow runs (default: 1)",
    )
    parser.add_argument(
        "--scenario",
        choices=list(VALID_SCENARIOS),
        default="all",
        help="Traffic scenario to execute (default: all)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for live traffic driver."""
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    results = asyncio.run(
        drive_live_traffic(
            target_url=args.target_url,
            concurrency=args.concurrency,
            scenario=args.scenario,
        )
    )
    failures = [r for r in results if not r.succeeded]
    for r in results:
        status_str = "OK" if r.succeeded else f"FAILED ({r.error})"
        print(
            f"[{r.scenario}] user={r.user_sub} order={r.order_id} steps={len(r.steps)} -> {status_str}"
        )

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
