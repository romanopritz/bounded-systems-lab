import asyncio

from httpx import ASGITransport

from bounded_systems_lab.api import ServiceSettings, create_app
from bounded_systems_lab.game_day_report import render_report
from bounded_systems_lab.loadgen import LoadConfig, run_load


def test_load_generator_has_a_fixed_request_budget_and_observes_rejection() -> None:
    async def scenario() -> None:
        app = create_app(
            ServiceSettings(
                max_concurrency=1,
                max_queue_size=0,
                work_timeout_seconds=0.5,
            )
        )
        report = await run_load(
            LoadConfig(
                target_url="http://service",
                requests=10,
                concurrency=5,
                duration_ms=50,
                request_timeout_seconds=1,
            ),
            transport=ASGITransport(app=app),
        )

        assert report.attempted == 10
        assert report.status_counts["200"] >= 1
        assert report.status_counts["503"] >= 1
        assert sum(report.status_counts.values()) == 10
        assert report.latency.maximum_ms < 500
        assert "service" not in report.model_dump_json()

    asyncio.run(scenario())


def test_load_generator_captures_dependency_scenarios_and_recovery() -> None:
    async def scenario() -> None:
        app = create_app(
            ServiceSettings(
                work_timeout_seconds=0.01,
                enable_fault_injection=True,
            )
        )
        transport = ASGITransport(app=app)

        latency = await run_load(
            LoadConfig(
                target_url="http://service",
                scenario="dependency_latency",
                requests=1,
                concurrency=1,
                duration_ms=5,
            ),
            transport=transport,
        )
        failure = await run_load(
            LoadConfig(
                target_url="http://service",
                scenario="dependency_failure",
                requests=1,
                concurrency=1,
                duration_ms=0,
            ),
            transport=transport,
        )
        timeout = await run_load(
            LoadConfig(
                target_url="http://service",
                scenario="dependency_timeout",
                requests=1,
                concurrency=1,
                duration_ms=0,
            ),
            transport=transport,
        )
        recovery = await run_load(
            LoadConfig(
                target_url="http://service",
                requests=1,
                concurrency=1,
                duration_ms=0,
            ),
            transport=transport,
        )

        assert latency.status_counts == {"200": 1}
        assert failure.status_counts == {"502": 1}
        assert timeout.status_counts == {"504": 1}
        assert recovery.status_counts == {"200": 1}

        incident = render_report(
            [latency, failure, timeout, recovery],
            alerts_verified=True,
            recovery_verified=True,
            gitops_rollback_verified=True,
        )
        assert "Alerts observed and cleared: verified" in incident
        assert "Targets and infrastructure identifiers" in incident
        assert "http://service" not in incident

    asyncio.run(scenario())
