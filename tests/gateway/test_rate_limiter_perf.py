"""Automated performance and latency benchmark for Gateway rate limiter."""

from __future__ import annotations

import statistics
import time
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from services.gateway.rate_limit_middleware import RateLimitMiddleware
from services.gateway.rate_limiter import InMemoryTokenBucketRateLimiter

RESULTS_FILE = Path(__file__).parent / "perf_results.txt"


def create_baseline_app() -> FastAPI:
    """Create raw FastAPI application without rate limiting."""
    app = FastAPI()

    @app.get("/benchmark")
    def benchmark_endpoint() -> dict[str, str]:
        return {"status": "ok"}

    return app


def create_rate_limited_app(capacity: float = 1000.0, refill_rate: float = 1000.0) -> FastAPI:
    """Create FastAPI application with RateLimitMiddleware active."""
    app = FastAPI()
    limiter = InMemoryTokenBucketRateLimiter(capacity=capacity, refill_rate=refill_rate)
    app.add_middleware(RateLimitMiddleware, limiter=limiter)

    @app.get("/benchmark")
    def benchmark_endpoint() -> dict[str, str]:
        return {"status": "ok"}

    return app


@pytest.mark.asyncio
async def test_rate_limiter_latency_and_throughput_benchmark() -> None:
    """Benchmark 500 requests asserting sub-1.5ms added latency and zero false drops."""
    num_requests = 500
    baseline_app = create_baseline_app()
    limited_app = create_rate_limited_app(capacity=1000.0, refill_rate=1000.0)

    transport_baseline = httpx.ASGITransport(app=baseline_app)
    transport_limited = httpx.ASGITransport(app=limited_app)

    async with (
        httpx.AsyncClient(transport=transport_baseline, base_url="http://test") as client_base,
        httpx.AsyncClient(transport=transport_limited, base_url="http://test") as client_lim,
    ):
        # Warmup phase (20 requests each)
        for _ in range(20):
            await client_base.get("/benchmark")
            await client_lim.get("/benchmark")

        # 1. Baseline latency measurement (500 requests)
        baseline_latencies: list[float] = []
        for _ in range(num_requests):
            t0 = time.perf_counter()
            resp = await client_base.get("/benchmark")
            t1 = time.perf_counter()
            assert resp.status_code == 200
            baseline_latencies.append((t1 - t0) * 1000.0)  # ms

        # 2. Rate-limited latency measurement (500 requests)
        limited_latencies: list[float] = []
        status_codes: list[int] = []
        bench_start = time.perf_counter()
        for _ in range(num_requests):
            t0 = time.perf_counter()
            resp = await client_lim.get("/benchmark")
            t1 = time.perf_counter()
            status_codes.append(resp.status_code)
            limited_latencies.append((t1 - t0) * 1000.0)  # ms
        bench_end = time.perf_counter()

    # 3. Assertions
    # Assertion A: Zero false drops under non-burst traffic
    drops = sum(1 for sc in status_codes if sc != 200)
    assert drops == 0, f"Encountered {drops} dropped requests under non-burst traffic"
    assert all(sc == 200 for sc in status_codes)

    # Calculate statistics
    med_base = statistics.median(baseline_latencies)
    med_lim = statistics.median(limited_latencies)
    med_added = max(0.0, med_lim - med_base)
    mean_base = statistics.mean(baseline_latencies)
    mean_lim = statistics.mean(limited_latencies)
    mean_added = max(0.0, mean_lim - mean_base)

    p95_base = statistics.quantiles(baseline_latencies, n=100)[94]
    p95_lim = statistics.quantiles(limited_latencies, n=100)[94]
    p95_added = max(0.0, p95_lim - p95_base)

    p99_base = statistics.quantiles(baseline_latencies, n=100)[98]
    p99_lim = statistics.quantiles(limited_latencies, n=100)[98]
    p99_added = max(0.0, p99_lim - p99_base)

    total_duration = bench_end - bench_start
    throughput = num_requests / max(total_duration, 1e-6)

    # Assertion B: Median added latency < 1.5 ms
    assert med_added < 1.5, f"Median added latency {med_added:.4f} ms exceeded 1.5 ms limit"

    # 4. Record metrics to tests/gateway/perf_results.txt
    report_lines = [
        "=" * 80,
        "GATEWAY RATE LIMITER PERFORMANCE BENCHMARK RESULTS",
        "=" * 80,
        f"Total Requests Evaluated:        {num_requests}",
        f"Allowed Requests (HTTP 200):     {num_requests - drops}",
        f"Dropped Requests (HTTP 429):     {drops}",
        f"False Drop Rate:                 {(drops / num_requests) * 100:.2f}%",
        "",
        "Latency Breakdown:",
        f"  Baseline App Median Latency:       {med_base:.4f} ms",
        f"  Rate-Limited App Median Latency:   {med_lim:.4f} ms",
        f"  Median Added Latency (Overhead):   {med_added:.4f} ms (Target: < 1.5000 ms)",
        f"  Mean Added Latency:                {mean_added:.4f} ms",
        f"  95th Percentile Added Latency:     {p95_added:.4f} ms",
        f"  99th Percentile Added Latency:     {p99_added:.4f} ms",
        "",
        "Throughput Performance:",
        f"  Total Benchmark Duration:          {total_duration:.4f} s",
        f"  Throughput:                        {throughput:.2f} req/s",
        "",
        "Conclusion:",
        "  PASS: Rate limiter introduces negligible overhead (well below 1.5ms budget).",
        "=" * 80,
    ]
    RESULTS_FILE.write_text("\n".join(report_lines) + "\n", encoding="utf-8")
    assert RESULTS_FILE.exists()
