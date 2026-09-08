import asyncio
import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from bounded_systems_lab.sre_assistant import DEVELOPER_PROMPT, SreAssistant
from bounded_systems_lab.sre_provider import FakeProvider
from bounded_systems_lab.sre_types import ModelResponse, ToolCall, ToolSpec


class _EvalStatus(BaseModel):
    running: int = 1
    queued: int = 0
    capacity: int = 6


class _EvalStatusTool:
    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="service_status",
            description="Read status.",
            parameters={"type": "object", "properties": {}},
        )

    async def invoke(self, arguments: dict[str, Any]) -> BaseModel:
        assert arguments == {}
        return _EvalStatus()


def test_deterministic_sre_cases_and_injection_boundary() -> None:
    cases: list[dict[str, Any]] = json.loads(
        (Path(__file__).parents[1] / "evals" / "assistant_cases.json").read_text()
    )

    async def scenario(case: dict[str, Any]) -> None:
        responses = []
        for step in case["steps"]:
            calls = tuple(
                ToolCall.model_validate(call) for call in step.get("tool_calls", [])
            )
            responses.append(
                ModelResponse(content=step.get("content", ""), tool_calls=calls)
            )
        provider = FakeProvider(responses)
        assistant = SreAssistant(provider=provider, tools=[_EvalStatusTool()])

        result = await assistant.ask(case["question"])

        assert result.answer == case["expected_answer"], case["name"]
        assert (
            result.stats.rejected_tool_calls == case["expected_rejected_tool_calls"]
        ), case["name"]
        first_request = provider.requests[0]
        assert first_request.messages[0].content == DEVELOPER_PROMPT
        assert first_request.messages[0].role == "developer"
        assert first_request.messages[1].content == case["question"]
        assert first_request.messages[1].role == "user"
        assert {tool.name for tool in first_request.tools} == {"service_status"}

    for case in cases:
        asyncio.run(scenario(case))
