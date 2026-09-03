"""Observable HTTP boundary for the bounded workload runner."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from fastapi import FastAPI, HTTPException, Request, Response
from pydantic import BaseModel, Field
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

from bounded_systems_lab.overload import BoundedAsyncRunner, WorkRejected

LOGGER = logging.getLogger("bounded_systems_lab.api")
QUIET_PATHS = frozenset({"/healthz", "/readyz", "/metrics"})
Workload = Callable[[int], Awaitable[str]]


@dataclass(frozen=True)
class ServiceSettings:
    max_concurrency: int = 2
    max_queue_size: int = 4
    work_timeout_seconds: float = 2.0

    @classmethod
    def from_env(cls) -> ServiceSettings:
        return cls(
            max_concurrency=_read_int("LAB_MAX_CONCURRENCY", 2, minimum=1),
            max_queue_size=_read_int("LAB_MAX_QUEUE_SIZE", 4, minimum=0),
            work_timeout_seconds=_read_float(
                "LAB_WORK_TIMEOUT_SECONDS", 2.0, minimum=0.001
            ),
        )


class WorkRequest(BaseModel):
    duration_ms: int = Field(default=250, ge=0, le=5_000)


class WorkResponse(BaseModel):
    request_id: str
    outcome: str
    duration_ms: int
    result: str


class ServiceMetrics:
    def __init__(self) -> None:
        self.registry = CollectorRegistry(auto_describe=True)
        self.requests = Counter(
            "bounded_work_requests_total",
            "Work requests by terminal outcome.",
            labelnames=("outcome",),
            registry=self.registry,
        )
        self.duration = Histogram(
            "bounded_work_request_duration_seconds",
            "End-to-end work request duration, including queue wait.",
            labelnames=("outcome",),
            buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 5),
            registry=self.registry,
        )
        self.running = Gauge(
            "bounded_work_running",
            "Work items currently executing.",
            registry=self.registry,
        )
        self.queued = Gauge(
            "bounded_work_queued",
            "Accepted work items waiting for an execution slot.",
            registry=self.registry,
        )
        self.capacity = Gauge(
            "bounded_work_capacity",
            "Maximum running plus queued work items.",
            registry=self.registry,
        )

    def observe(self, outcome: str, duration_seconds: float) -> None:
        self.requests.labels(outcome=outcome).inc()
        self.duration.labels(outcome=outcome).observe(duration_seconds)


async def simulated_work(duration_ms: int) -> str:
    await asyncio.sleep(duration_ms / 1_000)
    return "simulated-work-complete"


def create_app(
    settings: ServiceSettings | None = None,
    workload: Workload | None = None,
) -> FastAPI:
    service_settings = settings or ServiceSettings.from_env()
    execute = workload or simulated_work
    runner = BoundedAsyncRunner(
        max_concurrency=service_settings.max_concurrency,
        max_queue_size=service_settings.max_queue_size,
    )
    metrics = ServiceMetrics()
    metrics.capacity.set(
        service_settings.max_concurrency + service_settings.max_queue_size
    )

    application = FastAPI(title="Bounded Systems Lab", version="0.1.0")
    application.state.runner = runner
    application.state.settings = service_settings

    @application.middleware("http")
    async def request_context(request: Request, call_next):
        request_id = _request_id(request)
        request.state.request_id = request_id
        started = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            response.headers["X-Request-ID"] = request_id
            return response
        finally:
            if request.url.path not in QUIET_PATHS:
                LOGGER.info(
                    json.dumps(
                        {
                            "event": "http_request",
                            "request_id": request_id,
                            "method": request.method,
                            "path": request.url.path,
                            "status_code": status_code,
                            "duration_ms": round(
                                (time.perf_counter() - started) * 1_000, 2
                            ),
                        },
                        separators=(",", ":"),
                    )
                )

    @application.get("/healthz")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @application.get("/readyz")
    async def readiness() -> dict[str, str]:
        return {"status": "ready"}

    @application.get("/status")
    async def status() -> dict[str, int]:
        snapshot = await runner.snapshot()
        return {
            "accepted": snapshot.accepted,
            "running": snapshot.running,
            "queued": snapshot.queued,
            "capacity": snapshot.capacity,
        }

    @application.get("/metrics")
    async def prometheus_metrics() -> Response:
        snapshot = await runner.snapshot()
        metrics.running.set(snapshot.running)
        metrics.queued.set(snapshot.queued)
        return Response(
            content=generate_latest(metrics.registry),
            headers={"Content-Type": CONTENT_TYPE_LATEST},
        )

    @application.post("/v1/work", response_model=WorkResponse)
    async def work(payload: WorkRequest, request: Request) -> WorkResponse:
        started = time.perf_counter()
        outcome = "failed"
        try:
            result = await runner.run(
                lambda: execute(payload.duration_ms),
                timeout_seconds=service_settings.work_timeout_seconds,
            )
            outcome = "completed"
        except WorkRejected as exc:
            outcome = "rejected"
            raise HTTPException(
                status_code=503,
                detail="service capacity exhausted",
                headers={"Retry-After": "1"},
            ) from exc
        except TimeoutError as exc:
            outcome = "timed_out"
            raise HTTPException(
                status_code=504, detail="work deadline exceeded"
            ) from exc
        finally:
            metrics.observe(outcome, time.perf_counter() - started)

        return WorkResponse(
            request_id=request.state.request_id,
            outcome=outcome,
            duration_ms=payload.duration_ms,
            result=result,
        )

    return application


def _request_id(request: Request) -> str:
    supplied = request.headers.get("x-request-id", "").strip()
    if supplied and len(supplied) <= 128:
        return supplied
    return uuid.uuid4().hex


def _read_int(name: str, default: int, *, minimum: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if value < minimum:
        raise ValueError(f"{name} must be at least {minimum}")
    return value


def _read_float(name: str, default: float, *, minimum: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be numeric") from exc
    if value < minimum:
        raise ValueError(f"{name} must be at least {minimum}")
    return value


if not LOGGER.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    LOGGER.addHandler(handler)
    LOGGER.setLevel(logging.INFO)
    LOGGER.propagate = False

app = create_app()
