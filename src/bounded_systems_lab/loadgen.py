"""Bounded HTTP load generator for repeatable overload exercises."""

from __future__ import annotations

import argparse
import asyncio
import math
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator

from bounded_systems_lab.api import WorkScenario


class LoadConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    target_url: str = Field(exclude=True)
    scenario: WorkScenario = "normal"
    requests: int = Field(default=100, ge=1, le=10_000)
    concurrency: int = Field(default=12, ge=1, le=100)
    duration_ms: int = Field(default=1_500, ge=0, le=5_000)
    inter_request_delay_ms: int = Field(default=0, ge=0, le=10_000)
    request_timeout_seconds: float = Field(default=4.0, gt=0, le=30)

    @field_validator("target_url")
    @classmethod
    def validate_target_url(cls, value: str) -> str:
        if not value.startswith(("http://", "https://")):
            raise ValueError("target URL must use HTTP or HTTPS")
        return f"{value.rstrip('/')}/"


class LatencySummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    minimum_ms: float
    mean_ms: float
    p50_ms: float
    p95_ms: float
    p99_ms: float
    maximum_ms: float


class LoadReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: Literal[1] = 1
    started_at: datetime
    scenario: WorkScenario
    client_budget: dict[str, int | float]
    attempted: int
    status_counts: dict[str, int]
    transport_errors: dict[str, int]
    latency: LatencySummary
    wall_time_ms: float


async def run_load(
    config: LoadConfig,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> LoadReport:
    started_at = datetime.now(UTC)
    started = time.perf_counter()
    observations: list[tuple[str, float]] = []
    transport_errors: Counter[str] = Counter()
    next_request = 0
    index_lock = asyncio.Lock()

    async def reserve_request() -> bool:
        nonlocal next_request
        async with index_lock:
            if next_request >= config.requests:
                return False
            next_request += 1
            return True

    async with httpx.AsyncClient(
        base_url=config.target_url,
        transport=transport,
        timeout=httpx.Timeout(config.request_timeout_seconds),
        limits=httpx.Limits(
            max_connections=config.concurrency,
            max_keepalive_connections=config.concurrency,
        ),
    ) as client:

        async def worker() -> None:
            while await reserve_request():
                request_started = time.perf_counter()
                try:
                    response = await client.post(
                        "v1/work",
                        json={
                            "duration_ms": config.duration_ms,
                            "scenario": config.scenario,
                        },
                    )
                    outcome = str(response.status_code)
                except httpx.TimeoutException:
                    outcome = "client_timeout"
                    transport_errors[outcome] += 1
                except httpx.NetworkError:
                    outcome = "network_error"
                    transport_errors[outcome] += 1
                observations.append(
                    (outcome, (time.perf_counter() - request_started) * 1_000)
                )
                if config.inter_request_delay_ms:
                    await asyncio.sleep(config.inter_request_delay_ms / 1_000)

        await asyncio.gather(*(worker() for _ in range(config.concurrency)))

    latencies = sorted(duration for _, duration in observations)
    status_counts = Counter(
        outcome for outcome, _ in observations if outcome.isdecimal()
    )
    return LoadReport(
        started_at=started_at,
        scenario=config.scenario,
        client_budget={
            "requests": config.requests,
            "concurrency": config.concurrency,
            "duration_ms": config.duration_ms,
            "inter_request_delay_ms": config.inter_request_delay_ms,
            "request_timeout_seconds": config.request_timeout_seconds,
        },
        attempted=len(observations),
        status_counts=dict(sorted(status_counts.items())),
        transport_errors=dict(sorted(transport_errors.items())),
        latency=LatencySummary(
            minimum_ms=round(latencies[0], 3),
            mean_ms=round(sum(latencies) / len(latencies), 3),
            p50_ms=round(_percentile(latencies, 0.50), 3),
            p95_ms=round(_percentile(latencies, 0.95), 3),
            p99_ms=round(_percentile(latencies, 0.99), 3),
            maximum_ms=round(latencies[-1], 3),
        ),
        wall_time_ms=round((time.perf_counter() - started) * 1_000, 3),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a bounded HTTP load scenario.")
    parser.add_argument("--target-url", required=True)
    parser.add_argument(
        "--scenario",
        choices=(
            "normal",
            "dependency_latency",
            "dependency_failure",
            "dependency_timeout",
        ),
        default="normal",
    )
    parser.add_argument("--requests", type=int, default=100)
    parser.add_argument("--concurrency", type=int, default=12)
    parser.add_argument("--duration-ms", type=int, default=1_500)
    parser.add_argument("--inter-request-delay-ms", type=int, default=0)
    parser.add_argument("--request-timeout-seconds", type=float, default=4.0)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    config = LoadConfig(
        target_url=args.target_url,
        scenario=args.scenario,
        requests=args.requests,
        concurrency=args.concurrency,
        duration_ms=args.duration_ms,
        inter_request_delay_ms=args.inter_request_delay_ms,
        request_timeout_seconds=args.request_timeout_seconds,
    )
    asyncio.run(_run_cli(config, args.output))


async def _run_cli(config: LoadConfig, output: Path | None) -> None:
    report = await run_load(config)
    rendered = report.model_dump_json(indent=2)
    if output is not None:
        await asyncio.to_thread(output.write_text, f"{rendered}\n")
    print(rendered)


def _percentile(values: list[float], fraction: float) -> float:
    index = max(0, math.ceil(len(values) * fraction) - 1)
    return values[index]


if __name__ == "__main__":
    main()
