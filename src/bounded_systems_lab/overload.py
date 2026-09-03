"""Small overload-control primitive for async services.

The point of this module is not to replace a production queue. It makes the
capacity contract concrete: every service needs an explicit capacity envelope
and a deliberate rejection path when the envelope is full.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TypeVar

T = TypeVar("T")


class WorkRejected(RuntimeError):
    """Raised when the runner has no remaining admission capacity."""


@dataclass(frozen=True)
class RunnerSnapshot:
    """Point-in-time runner state useful for metrics and tests."""

    accepted: int
    running: int
    max_concurrency: int
    max_queue_size: int

    @property
    def queued(self) -> int:
        return max(0, self.accepted - self.running)

    @property
    def capacity(self) -> int:
        return self.max_concurrency + self.max_queue_size


class BoundedAsyncRunner:
    """Run async work with bounded concurrency and bounded waiting work."""

    def __init__(self, *, max_concurrency: int, max_queue_size: int) -> None:
        if max_concurrency < 1:
            msg = "max_concurrency must be at least 1"
            raise ValueError(msg)
        if max_queue_size < 0:
            msg = "max_queue_size cannot be negative"
            raise ValueError(msg)

        self._max_concurrency = max_concurrency
        self._max_queue_size = max_queue_size
        self._run_slots = asyncio.Semaphore(max_concurrency)
        self._lock = asyncio.Lock()
        self._accepted = 0
        self._running = 0

    async def run(
        self,
        operation: Callable[[], Awaitable[T]],
        *,
        timeout_seconds: float | None = None,
    ) -> T:
        """Run one operation or reject it immediately when capacity is full."""

        await self._admit()
        try:
            if timeout_seconds is None:
                return await self._run_admitted(operation)
            return await asyncio.wait_for(
                self._run_admitted(operation), timeout_seconds
            )
        finally:
            await self._release()

    async def _run_admitted(self, operation: Callable[[], Awaitable[T]]) -> T:
        async with self._run_slots:
            await self._mark_started()
            try:
                return await operation()
            finally:
                await self._mark_finished()

    async def snapshot(self) -> RunnerSnapshot:
        async with self._lock:
            return RunnerSnapshot(
                accepted=self._accepted,
                running=self._running,
                max_concurrency=self._max_concurrency,
                max_queue_size=self._max_queue_size,
            )

    async def _admit(self) -> None:
        async with self._lock:
            capacity = self._max_concurrency + self._max_queue_size
            if self._accepted >= capacity:
                raise WorkRejected("runner capacity exhausted")
            self._accepted += 1

    async def _release(self) -> None:
        async with self._lock:
            self._accepted -= 1

    async def _mark_started(self) -> None:
        async with self._lock:
            self._running += 1

    async def _mark_finished(self) -> None:
        async with self._lock:
            self._running -= 1
