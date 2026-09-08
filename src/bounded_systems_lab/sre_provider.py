"""OpenAI-compatible and deterministic model providers."""

from __future__ import annotations

import json
from collections import deque
from collections.abc import Sequence
from typing import Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from bounded_systems_lab.sre_types import (
    ChatMessage,
    ModelProvider,
    ModelRequest,
    ModelResponse,
    ModelUsage,
    ToolCall,
)


class ProviderError(RuntimeError):
    """Model-provider failure with a bounded metric category."""

    def __init__(self, message: str, *, retryable: bool, category: str) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.category = category


class _WireFunction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    arguments: str


class _WireToolCall(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    type: Literal["function"]
    function: _WireFunction


class _WireMessage(BaseModel):
    model_config = ConfigDict(extra="ignore")

    content: str | None = None
    tool_calls: list[_WireToolCall] = Field(default_factory=list)


class _WireChoice(BaseModel):
    model_config = ConfigDict(extra="ignore")

    message: _WireMessage
    finish_reason: str | None = None


class _WireUsage(BaseModel):
    model_config = ConfigDict(extra="ignore")

    prompt_tokens: int = 0
    completion_tokens: int = 0


class _WireResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    choices: list[_WireChoice] = Field(min_length=1)
    usage: _WireUsage = _WireUsage()


class OpenAICompatibleProvider(ModelProvider):
    """Minimal Chat Completions client with no implicit retries."""

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str | None = None,
        token_parameter: Literal["max_completion_tokens", "max_tokens"] = (
            "max_completion_tokens"
        ),
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not base_url.startswith(("http://", "https://")):
            raise ValueError("model base URL must use HTTP or HTTPS")
        if not model.strip():
            raise ValueError("model name cannot be empty")

        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._model = model
        self._token_parameter = token_parameter
        self._client = httpx.AsyncClient(
            base_url=f"{base_url.rstrip('/')}/",
            headers=headers,
            transport=transport,
        )

    async def complete(self, request: ModelRequest) -> ModelResponse:
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [_message_payload(message) for message in request.messages],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.parameters,
                        "strict": True,
                    },
                }
                for tool in request.tools
            ],
            "tool_choice": "auto",
            "parallel_tool_calls": False,
            self._token_parameter: request.max_output_tokens,
        }

        try:
            response = await self._client.post(
                "chat/completions",
                json=payload,
                timeout=httpx.Timeout(request.timeout_seconds),
            )
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise ProviderError(
                "model request timed out", retryable=True, category="timeout"
            ) from exc
        except httpx.NetworkError as exc:
            raise ProviderError(
                "model network request failed", retryable=True, category="network"
            ) from exc
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            retryable = status == 429 or status >= 500
            category = "rate_limited" if status == 429 else "http_error"
            raise ProviderError(
                f"model provider returned HTTP {status}",
                retryable=retryable,
                category=category,
            ) from exc

        try:
            wire = _WireResponse.model_validate(response.json())
            choice = wire.choices[0]
            tool_calls = tuple(
                _parse_tool_call(call) for call in choice.message.tool_calls
            )
        except (ValidationError, ValueError, json.JSONDecodeError) as exc:
            raise ProviderError(
                "model provider returned an invalid response",
                retryable=False,
                category="protocol",
            ) from exc

        return ModelResponse(
            content=choice.message.content or "",
            tool_calls=tool_calls,
            usage=ModelUsage(
                input_tokens=wire.usage.prompt_tokens,
                output_tokens=wire.usage.completion_tokens,
            ),
            finish_reason=choice.finish_reason or "unknown",
        )

    async def aclose(self) -> None:
        await self._client.aclose()


class FakeProvider(ModelProvider):
    """Deterministic provider for tests and evaluations."""

    def __init__(self, steps: Sequence[ModelResponse | ProviderError]) -> None:
        self._steps = deque(steps)
        self.requests: list[ModelRequest] = []

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        if not self._steps:
            raise ProviderError(
                "fake provider has no remaining response",
                retryable=False,
                category="fake_exhausted",
            )
        step = self._steps.popleft()
        if isinstance(step, ProviderError):
            raise step
        return step


def _message_payload(message: ChatMessage) -> dict[str, Any]:
    payload: dict[str, Any] = {"role": message.role, "content": message.content}
    if message.tool_call_id is not None:
        payload["tool_call_id"] = message.tool_call_id
    if message.tool_calls:
        payload["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": json.dumps(
                        call.arguments, separators=(",", ":"), sort_keys=True
                    ),
                },
            }
            for call in message.tool_calls
        ]
    return payload


def _parse_tool_call(call: _WireToolCall) -> ToolCall:
    arguments = json.loads(call.function.arguments)
    if not isinstance(arguments, dict) or not all(
        isinstance(key, str) for key in arguments
    ):
        raise ValueError("tool arguments must be a JSON object")
    return ToolCall(id=call.id, name=call.function.name, arguments=arguments)
