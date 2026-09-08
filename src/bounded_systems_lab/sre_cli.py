"""One-shot CLI for the operator-side read-only SRE assistant."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path
from typing import Literal, cast

import httpx

from bounded_systems_lab.sre_assistant import (
    AssistantBusy,
    AssistantDeadlineExceeded,
    AssistantLimitExceeded,
    SreAssistant,
)
from bounded_systems_lab.sre_provider import OpenAICompatibleProvider, ProviderError
from bounded_systems_lab.sre_tools import build_read_only_tools


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ask a bounded, read-only SRE question."
    )
    parser.add_argument("question")
    parser.add_argument(
        "--metrics-file",
        type=Path,
        help="Write this run's Prometheus metrics to a local file.",
    )
    args = parser.parse_args()
    try:
        output = asyncio.run(_run(args.question, args.metrics_file))
    except (ValueError, AssistantBusy, AssistantDeadlineExceeded) as exc:
        parser.exit(2, f"error: {exc}\n")
    except (AssistantLimitExceeded, ProviderError) as exc:
        parser.exit(1, f"error: {exc}\n")
    print(output)


async def _run(question: str, metrics_file: Path | None) -> str:
    base_url = _required_env("LAB_AI_BASE_URL")
    model = _required_env("LAB_AI_MODEL")
    token_parameter = cast(
        Literal["max_completion_tokens", "max_tokens"],
        os.getenv("LAB_AI_TOKEN_PARAMETER", "max_completion_tokens"),
    )
    if token_parameter not in {"max_completion_tokens", "max_tokens"}:
        raise ValueError(
            "LAB_AI_TOKEN_PARAMETER must be max_completion_tokens or max_tokens"
        )

    timeout = httpx.Timeout(2.0)
    limits = httpx.Limits(max_connections=2, max_keepalive_connections=2)
    async with (
        httpx.AsyncClient(
            base_url=f"{os.getenv('LAB_SERVICE_URL', 'http://127.0.0.1:18000').rstrip('/')}/",
            timeout=timeout,
            limits=limits,
        ) as service_client,
        httpx.AsyncClient(
            base_url=f"{os.getenv('LAB_PROMETHEUS_URL', 'http://127.0.0.1:19090').rstrip('/')}/",
            timeout=timeout,
            limits=limits,
        ) as prometheus_client,
    ):
        provider = OpenAICompatibleProvider(
            base_url=base_url,
            model=model,
            api_key=os.getenv("LAB_AI_API_KEY"),
            token_parameter=token_parameter,
        )
        assistant = SreAssistant(
            provider=provider,
            tools=build_read_only_tools(
                service_client=service_client,
                prometheus_client=prometheus_client,
            ),
        )
        try:
            result = await assistant.ask(question)
        finally:
            await provider.aclose()

    if metrics_file is not None:
        metrics_file.write_bytes(assistant.metrics.render())
    return result.model_dump_json(indent=2)


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"{name} is required")
    return value


if __name__ == "__main__":
    sys.exit(main())
