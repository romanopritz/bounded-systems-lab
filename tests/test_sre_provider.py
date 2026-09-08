import asyncio
import json
from typing import Any

import httpx
import pytest

from bounded_systems_lab.sre_provider import OpenAICompatibleProvider, ProviderError
from bounded_systems_lab.sre_types import ChatMessage, ModelRequest, ToolSpec


def test_openai_compatible_provider_serializes_tools_and_parses_calls() -> None:
    async def scenario() -> None:
        captured: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            captured["authorization"] = request.headers.get("authorization")
            captured["payload"] = json.loads(request.content)
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "content": None,
                                "tool_calls": [
                                    {
                                        "id": "call-1",
                                        "type": "function",
                                        "function": {
                                            "name": "service_status",
                                            "arguments": "{}",
                                        },
                                    }
                                ],
                            },
                            "finish_reason": "tool_calls",
                        }
                    ],
                    "usage": {"prompt_tokens": 17, "completion_tokens": 4},
                },
            )

        provider = OpenAICompatibleProvider(
            base_url="https://model.example/v1",
            model="test-model",
            api_key="test-secret",
            transport=httpx.MockTransport(handler),
        )
        try:
            response = await provider.complete(
                ModelRequest(
                    messages=(ChatMessage(role="user", content="status?"),),
                    tools=(
                        ToolSpec(
                            name="service_status",
                            description="Read status.",
                            parameters={"type": "object", "properties": {}},
                        ),
                    ),
                    max_output_tokens=123,
                    timeout_seconds=1,
                )
            )
        finally:
            await provider.aclose()

        assert captured["url"] == "https://model.example/v1/chat/completions"
        assert captured["authorization"] == "Bearer test-secret"
        payload = captured["payload"]
        assert payload["max_completion_tokens"] == 123
        assert payload["parallel_tool_calls"] is False
        assert payload["tools"][0]["function"]["strict"] is True
        assert response.tool_calls[0].name == "service_status"
        assert response.tool_calls[0].arguments == {}
        assert response.usage.input_tokens == 17

    asyncio.run(scenario())


def test_openai_compatible_provider_marks_rate_limit_as_retryable() -> None:
    async def scenario() -> None:
        provider = OpenAICompatibleProvider(
            base_url="https://model.example/v1",
            model="test-model",
            transport=httpx.MockTransport(
                lambda _: httpx.Response(429, json={"error": "slow down"})
            ),
        )
        try:
            with pytest.raises(ProviderError) as raised:
                await provider.complete(
                    ModelRequest(
                        messages=(ChatMessage(role="user", content="status?"),),
                        tools=(),
                        max_output_tokens=10,
                        timeout_seconds=1,
                    )
                )
        finally:
            await provider.aclose()

        assert raised.value.retryable is True
        assert raised.value.category == "rate_limited"

    asyncio.run(scenario())
