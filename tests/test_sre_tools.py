import asyncio

import httpx
import pytest

from bounded_systems_lab.sre_tools import (
    ActiveAlertsTool,
    IndicatorTool,
    ServiceStatusTool,
    ToolError,
)


def test_service_status_tool_rejects_undeclared_arguments() -> None:
    async def scenario() -> None:
        async with httpx.AsyncClient(
            base_url="http://service/",
            transport=httpx.MockTransport(
                lambda _: httpx.Response(
                    200,
                    json={"accepted": 1, "running": 1, "queued": 0, "capacity": 6},
                )
            ),
        ) as client:
            tool = ServiceStatusTool(client)
            status = await tool.invoke({})
            assert status.model_dump()["capacity"] == 6
            with pytest.raises(ToolError):
                await tool.invoke({"url": "http://metadata/"})

    asyncio.run(scenario())


def test_indicator_tool_maps_enum_to_fixed_promql() -> None:
    async def scenario() -> None:
        observed_query = ""

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal observed_query
            observed_query = request.url.params["query"]
            return httpx.Response(
                200,
                json={
                    "status": "success",
                    "data": {
                        "resultType": "vector",
                        "result": [{"metric": {}, "value": [1000.0, "0.999"]}],
                    },
                },
            )

        async with httpx.AsyncClient(
            base_url="http://prometheus/",
            transport=httpx.MockTransport(handler),
        ) as client:
            result = await IndicatorTool(client).invoke({"indicator": "availability"})

        assert observed_query == "bounded:sli_availability:ratio_rate5m"
        assert result.model_dump()["value"] == 0.999

    asyncio.run(scenario())


def test_active_alerts_tool_returns_only_bounded_fields() -> None:
    async def scenario() -> None:
        async with httpx.AsyncClient(
            base_url="http://prometheus/",
            transport=httpx.MockTransport(
                lambda _: httpx.Response(
                    200,
                    json={
                        "data": {
                            "alerts": [
                                {
                                    "labels": {
                                        "alertname": "HighSaturation",
                                        "severity": "warning",
                                        "pod": "internal-pod-name",
                                    },
                                    "annotations": {
                                        "summary": "Capacity is constrained"
                                    },
                                    "state": "firing",
                                    "value": "private detail",
                                }
                            ]
                        }
                    },
                )
            ),
        ) as client:
            result = await ActiveAlertsTool(client).invoke({})

        payload = result.model_dump()
        assert payload["count"] == 1
        assert payload["alerts"][0] == {
            "name": "HighSaturation",
            "severity": "warning",
            "state": "firing",
            "summary": "Capacity is constrained",
        }
        assert "internal-pod-name" not in result.model_dump_json()

    asyncio.run(scenario())
