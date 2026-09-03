import asyncio

from httpx import ASGITransport, AsyncClient

from bounded_systems_lab.api import ServiceSettings, create_app


def test_health_readiness_work_and_metrics() -> None:
    async def scenario() -> None:
        app = create_app(ServiceSettings())
        transport = ASGITransport(app=app)

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            assert (await client.get("/healthz")).json() == {"status": "ok"}
            assert (await client.get("/readyz")).json() == {"status": "ready"}

            response = await client.post(
                "/v1/work",
                json={"duration_ms": 0},
                headers={"X-Request-ID": "test-request"},
            )
            metrics = await client.get("/metrics")

        assert response.status_code == 200
        assert response.headers["x-request-id"] == "test-request"
        assert response.json() == {
            "request_id": "test-request",
            "outcome": "completed",
            "duration_ms": 0,
            "result": "simulated-work-complete",
        }
        assert 'bounded_work_requests_total{outcome="completed"} 1.0' in metrics.text
        assert "bounded_work_capacity 6.0" in metrics.text

    asyncio.run(scenario())


def test_unexpected_workload_error_is_counted_as_failed() -> None:
    async def scenario() -> None:
        async def broken(_: int) -> str:
            raise RuntimeError("simulated dependency failure")

        app = create_app(ServiceSettings(), workload=broken)
        transport = ASGITransport(app=app, raise_app_exceptions=False)

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/v1/work", json={"duration_ms": 1})
            metrics = await client.get("/metrics")

        assert response.status_code == 500
        assert 'bounded_work_requests_total{outcome="failed"} 1.0' in metrics.text

    asyncio.run(scenario())


def test_rejects_work_when_running_and_queue_capacity_are_full() -> None:
    async def scenario() -> None:
        release = asyncio.Event()

        async def blocked(_: int) -> str:
            await release.wait()
            return "released"

        app = create_app(
            ServiceSettings(max_concurrency=1, max_queue_size=1),
            workload=blocked,
        )
        transport = ASGITransport(app=app)

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            first = asyncio.create_task(
                client.post("/v1/work", json={"duration_ms": 1})
            )
            second = asyncio.create_task(
                client.post("/v1/work", json={"duration_ms": 1})
            )

            while (await app.state.runner.snapshot()).accepted < 2:
                await asyncio.sleep(0)

            rejected = await client.post("/v1/work", json={"duration_ms": 1})
            status = await client.get("/status")
            release.set()
            completed = await asyncio.gather(first, second)
            metrics = await client.get("/metrics")

        assert rejected.status_code == 503
        assert rejected.headers["retry-after"] == "1"
        assert status.json() == {
            "accepted": 2,
            "running": 1,
            "queued": 1,
            "capacity": 2,
        }
        assert all(response.status_code == 200 for response in completed)
        assert 'bounded_work_requests_total{outcome="rejected"} 1.0' in metrics.text

    asyncio.run(scenario())


def test_times_out_work_and_releases_capacity() -> None:
    async def scenario() -> None:
        async def slow(_: int) -> str:
            await asyncio.sleep(1)
            return "too-late"

        app = create_app(
            ServiceSettings(
                max_concurrency=1,
                max_queue_size=0,
                work_timeout_seconds=0.01,
            ),
            workload=slow,
        )
        transport = ASGITransport(app=app)

        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/v1/work", json={"duration_ms": 1})
            status = await client.get("/status")
            metrics = await client.get("/metrics")

        assert response.status_code == 504
        assert status.json()["accepted"] == 0
        assert status.json()["running"] == 0
        assert 'bounded_work_requests_total{outcome="timed_out"} 1.0' in metrics.text

    asyncio.run(scenario())
