import asyncio
from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict

from bounded_systems_lab.sre_assistant import (
    AssistantBusy,
    AssistantLimitExceeded,
    AssistantLimits,
    SreAssistant,
)
from bounded_systems_lab.sre_provider import FakeProvider, ProviderError
from bounded_systems_lab.sre_types import (
    ModelRequest,
    ModelResponse,
    ModelUsage,
    ToolCall,
    ToolSpec,
)


class _Status(BaseModel):
    model_config = ConfigDict(frozen=True)

    running: int
    queued: int
    capacity: int


class _StatusTool:
    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="service_status",
            description="Read status.",
            parameters={
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        )

    async def invoke(self, arguments: dict[str, Any]) -> BaseModel:
        assert arguments == {}
        return _Status(running=1, queued=0, capacity=6)


def test_assistant_runs_a_bounded_tool_loop() -> None:
    async def scenario() -> None:
        provider = FakeProvider(
            [
                ModelResponse(
                    tool_calls=(
                        ToolCall(id="call-1", name="service_status", arguments={}),
                    ),
                    usage=ModelUsage(input_tokens=10, output_tokens=3),
                ),
                ModelResponse(
                    content="The service is within capacity: 1 of 6 slots is running.",
                    usage=ModelUsage(input_tokens=20, output_tokens=12),
                ),
            ]
        )
        assistant = SreAssistant(provider=provider, tools=[_StatusTool()])

        result = await assistant.ask("Is the service saturated?")

        assert result.stats.model_calls == 2
        assert result.stats.tool_calls == 1
        assert result.stats.input_tokens == 30
        assert provider.requests[1].messages[-1].role == "tool"
        assert '"capacity":6' in provider.requests[1].messages[-1].content
        assert b'bounded_sre_runs_total{outcome="completed"} 1.0' in (
            assistant.metrics.render()
        )

    asyncio.run(scenario())


def test_assistant_retries_only_retryable_provider_errors() -> None:
    async def scenario() -> None:
        delays: list[float] = []

        async def record_delay(delay: float) -> None:
            delays.append(delay)

        provider = FakeProvider(
            [
                ProviderError("busy", retryable=True, category="rate_limited"),
                ModelResponse(content="Recovered."),
            ]
        )
        assistant = SreAssistant(
            provider=provider,
            tools=[],
            sleep=record_delay,
        )

        result = await assistant.ask("Status?")

        assert result.stats.retries == 1
        assert result.stats.model_calls == 2
        assert delays == [0.25]

    asyncio.run(scenario())


def test_assistant_rejects_excess_tool_calls_before_invocation() -> None:
    async def scenario() -> None:
        provider = FakeProvider(
            [
                ModelResponse(
                    tool_calls=(
                        ToolCall(id="one", name="service_status", arguments={}),
                        ToolCall(id="two", name="service_status", arguments={}),
                    )
                )
            ]
        )
        assistant = SreAssistant(
            provider=provider,
            tools=[_StatusTool()],
            limits=AssistantLimits(max_tool_calls=1),
        )

        with pytest.raises(AssistantLimitExceeded):
            await assistant.ask("Call everything.")

    asyncio.run(scenario())


def test_unknown_tool_is_rejected_and_never_invoked() -> None:
    async def scenario() -> None:
        provider = FakeProvider(
            [
                ModelResponse(
                    tool_calls=(
                        ToolCall(id="bad", name="restart_service", arguments={}),
                    )
                ),
                ModelResponse(content="I cannot perform write operations."),
            ]
        )
        assistant = SreAssistant(provider=provider, tools=[_StatusTool()])

        result = await assistant.ask("Restart it.")

        assert result.stats.rejected_tool_calls == 1
        assert "not allowlisted" in provider.requests[1].messages[-1].content
        assert {tool.name for tool in provider.requests[0].tools} == {"service_status"}

    asyncio.run(scenario())


def test_assistant_bounds_concurrency_and_queue() -> None:
    class BlockingProvider:
        def __init__(self) -> None:
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def complete(self, request: ModelRequest) -> ModelResponse:
            del request
            self.started.set()
            await self.release.wait()
            return ModelResponse(content="done")

    async def scenario() -> None:
        provider = BlockingProvider()
        assistant = SreAssistant(
            provider=provider,
            tools=[],
            limits=AssistantLimits(max_concurrency=1, max_queue_size=1),
        )

        first = asyncio.create_task(assistant.ask("first"))
        await provider.started.wait()
        second = asyncio.create_task(assistant.ask("second"))
        await asyncio.sleep(0)
        with pytest.raises(AssistantBusy):
            await assistant.ask("third")

        provider.release.set()
        results = await asyncio.gather(first, second)
        assert [result.answer for result in results] == ["done", "done"]

    asyncio.run(scenario())
