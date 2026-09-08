"""Narrow, read-only tools exposed to the SRE assistant."""

from __future__ import annotations

from typing import Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from bounded_systems_lab.sre_types import ToolSpec

MAX_RESPONSE_BYTES = 64 * 1024
MAX_ALERTS = 20
MAX_TEXT_CHARS = 500

IndicatorName = Literal[
    "availability",
    "latency",
    "rejection",
    "errors",
    "saturation",
    "availability_budget",
    "error_budget",
    "rejection_budget",
]

INDICATOR_QUERIES: dict[IndicatorName, str] = {
    "availability": "bounded:sli_availability:ratio_rate5m",
    "latency": "bounded:sli_latency_le_1s:ratio_rate5m",
    "rejection": "bounded:sli_rejection:ratio_rate5m",
    "errors": "bounded:sli_error:ratio_rate5m",
    "saturation": "bounded:sli_saturation:ratio",
    "availability_budget": "bounded:slo_availability_error_budget_remaining:ratio1h",
    "error_budget": "bounded:slo_error_budget_remaining:ratio1h",
    "rejection_budget": "bounded:slo_rejection_budget_remaining:ratio1h",
}


class ToolError(RuntimeError):
    """A bounded failure returned by an allowlisted tool."""


class _NoArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ServiceStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    accepted: int = Field(ge=0)
    running: int = Field(ge=0)
    queued: int = Field(ge=0)
    capacity: int = Field(ge=1)


class IndicatorArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    indicator: IndicatorName


class IndicatorValue(BaseModel):
    model_config = ConfigDict(frozen=True)

    indicator: IndicatorName
    value: float | None
    observed_at: float | None = None


class AlertSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    severity: str
    state: str
    summary: str


class ActiveAlerts(BaseModel):
    model_config = ConfigDict(frozen=True)

    count: int = Field(ge=0)
    truncated: bool
    alerts: tuple[AlertSummary, ...]


class ServiceStatusTool:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="service_status",
            description="Read the bounded service's current capacity state.",
            parameters=_NoArguments.model_json_schema(),
        )

    async def invoke(self, arguments: dict[str, Any]) -> BaseModel:
        _validate_arguments(_NoArguments, arguments)
        payload = await _get_json(self._client, "status")
        try:
            return ServiceStatus.model_validate(payload)
        except ValidationError as exc:
            raise ToolError("service returned an invalid status response") from exc


class IndicatorTool:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="slo_indicator",
            description=(
                "Read one allowlisted service-level indicator or error-budget value."
            ),
            parameters=IndicatorArguments.model_json_schema(),
        )

    async def invoke(self, arguments: dict[str, Any]) -> BaseModel:
        parsed = _validate_arguments(IndicatorArguments, arguments)
        payload = await _get_json(
            self._client,
            "api/v1/query",
            params={"query": INDICATOR_QUERIES[parsed.indicator]},
        )
        try:
            result = payload["data"]["result"]
            if not isinstance(result, list):
                raise TypeError
            if not result:
                return IndicatorValue(indicator=parsed.indicator, value=None)
            sample = result[0]["value"]
            if not isinstance(sample, list) or len(sample) != 2:
                raise TypeError
            return IndicatorValue(
                indicator=parsed.indicator,
                observed_at=float(sample[0]),
                value=float(sample[1]),
            )
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ToolError("Prometheus returned an invalid query response") from exc


class ActiveAlertsTool:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="active_alerts",
            description="Read active Prometheus alerts with a bounded label subset.",
            parameters=_NoArguments.model_json_schema(),
        )

    async def invoke(self, arguments: dict[str, Any]) -> BaseModel:
        _validate_arguments(_NoArguments, arguments)
        payload = await _get_json(self._client, "api/v1/alerts")
        try:
            raw_alerts = payload["data"]["alerts"]
            if not isinstance(raw_alerts, list):
                raise TypeError
            alerts = tuple(_sanitize_alert(alert) for alert in raw_alerts[:MAX_ALERTS])
        except (KeyError, TypeError) as exc:
            raise ToolError("Prometheus returned an invalid alerts response") from exc
        return ActiveAlerts(
            count=len(raw_alerts),
            truncated=len(raw_alerts) > MAX_ALERTS,
            alerts=alerts,
        )


def build_read_only_tools(
    *, service_client: httpx.AsyncClient, prometheus_client: httpx.AsyncClient
) -> tuple[ServiceStatusTool | IndicatorTool | ActiveAlertsTool, ...]:
    return (
        ServiceStatusTool(service_client),
        IndicatorTool(prometheus_client),
        ActiveAlertsTool(prometheus_client),
    )


def _validate_arguments[T: BaseModel](model: type[T], arguments: dict[str, Any]) -> T:
    try:
        return model.model_validate(arguments)
    except ValidationError as exc:
        raise ToolError("tool arguments did not match the declared schema") from exc


async def _get_json(
    client: httpx.AsyncClient,
    path: str,
    *,
    params: dict[str, str] | None = None,
) -> Any:
    try:
        response = await client.get(path, params=params, timeout=2.0)
        response.raise_for_status()
    except httpx.TimeoutException as exc:
        raise ToolError("read-only dependency request timed out") from exc
    except (httpx.NetworkError, httpx.HTTPStatusError) as exc:
        raise ToolError("read-only dependency request failed") from exc
    if len(response.content) > MAX_RESPONSE_BYTES:
        raise ToolError("read-only dependency response exceeded the size limit")
    try:
        return response.json()
    except ValueError as exc:
        raise ToolError("read-only dependency returned invalid JSON") from exc


def _sanitize_alert(raw: Any) -> AlertSummary:
    if not isinstance(raw, dict):
        raise TypeError
    labels = raw.get("labels", {})
    annotations = raw.get("annotations", {})
    if not isinstance(labels, dict) or not isinstance(annotations, dict):
        raise TypeError
    return AlertSummary(
        name=_bounded_text(labels.get("alertname", "unknown")),
        severity=_bounded_text(labels.get("severity", "unknown")),
        state=_bounded_text(raw.get("state", "unknown")),
        summary=_bounded_text(annotations.get("summary", "")),
    )


def _bounded_text(value: Any) -> str:
    return str(value)[:MAX_TEXT_CHARS]
