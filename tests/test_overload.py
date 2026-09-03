import asyncio

import pytest

from bounded_systems_lab import BoundedAsyncRunner, WorkRejected


def test_rejects_when_running_and_waiting_work_reach_capacity() -> None:
    async def scenario() -> None:
        runner = BoundedAsyncRunner(max_concurrency=1, max_queue_size=1)
        release = asyncio.Event()

        async def blocked() -> str:
            await release.wait()
            return "ok"

        first = asyncio.create_task(runner.run(blocked))
        second = asyncio.create_task(runner.run(blocked))
        await asyncio.sleep(0)

        with pytest.raises(WorkRejected):
            await runner.run(blocked)

        snapshot = await runner.snapshot()
        assert snapshot.accepted == 2
        assert snapshot.running == 1
        assert snapshot.queued == 1

        release.set()
        assert await first == "ok"
        assert await second == "ok"

    asyncio.run(scenario())


def test_never_runs_more_than_the_concurrency_limit() -> None:
    async def scenario() -> None:
        runner = BoundedAsyncRunner(max_concurrency=2, max_queue_size=3)
        current = 0
        observed_max = 0
        lock = asyncio.Lock()

        async def measured() -> int:
            nonlocal current, observed_max
            async with lock:
                current += 1
                observed_max = max(observed_max, current)
            await asyncio.sleep(0.01)
            async with lock:
                current -= 1
            return observed_max

        results = await asyncio.gather(*(runner.run(measured) for _ in range(5)))

        assert max(results) == 2
        assert (await runner.snapshot()).accepted == 0

    asyncio.run(scenario())


def test_timeout_releases_capacity() -> None:
    async def scenario() -> None:
        runner = BoundedAsyncRunner(max_concurrency=1, max_queue_size=0)

        async def slow() -> None:
            await asyncio.sleep(10)

        with pytest.raises(TimeoutError):
            await runner.run(slow, timeout_seconds=0.01)

        assert (await runner.snapshot()).accepted == 0
        assert (
            await runner.run(lambda: asyncio.sleep(0, result="recovered"))
            == "recovered"
        )

    asyncio.run(scenario())


def test_timeout_includes_time_waiting_for_a_run_slot() -> None:
    async def scenario() -> None:
        runner = BoundedAsyncRunner(max_concurrency=1, max_queue_size=1)
        release = asyncio.Event()

        async def blocked() -> str:
            await release.wait()
            return "done"

        first = asyncio.create_task(runner.run(blocked))
        while (await runner.snapshot()).running < 1:
            await asyncio.sleep(0)

        with pytest.raises(TimeoutError):
            await runner.run(blocked, timeout_seconds=0.01)

        snapshot = await runner.snapshot()
        assert snapshot.accepted == 1
        assert snapshot.running == 1
        assert snapshot.queued == 0

        release.set()
        assert await first == "done"

    asyncio.run(scenario())


def test_rejects_invalid_limits() -> None:
    with pytest.raises(ValueError):
        BoundedAsyncRunner(max_concurrency=0, max_queue_size=1)

    with pytest.raises(ValueError):
        BoundedAsyncRunner(max_concurrency=1, max_queue_size=-1)
