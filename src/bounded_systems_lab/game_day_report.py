"""Render anonymized load evidence into a concise incident report."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from bounded_systems_lab.loadgen import LoadReport


def render_report(
    reports: list[LoadReport],
    *,
    alerts_verified: bool,
    recovery_verified: bool,
    gitops_rollback_verified: bool,
) -> str:
    lines = [
        "# Overload And Recovery Game Day",
        "",
        "## Scope",
        "",
        (
            f"Executed {len(reports)} bounded scenarios with fixed client request, "
            "concurrency, and timeout budgets. Targets and infrastructure identifiers "
            "are intentionally omitted from the evidence."
        ),
        "",
        "## Results",
        "",
        "| Scenario | Attempts | Statuses | p95 | Maximum |",
        "| --- | ---: | --- | ---: | ---: |",
    ]
    for report in reports:
        statuses = ", ".join(
            f"{status}: {count}" for status, count in report.status_counts.items()
        )
        errors = ", ".join(
            f"{name}: {count}" for name, count in report.transport_errors.items()
        )
        summary = "; ".join(part for part in (statuses, errors) if part) or "none"
        lines.append(
            f"| {report.scenario} | {report.attempted} | {summary} | "
            f"{report.latency.p95_ms:.1f} ms | {report.latency.maximum_ms:.1f} ms |"
        )

    lines.extend(
        [
            "",
            "## Verification",
            "",
            f"- Alerts observed and cleared: {_yes_no(alerts_verified)}",
            f"- Service recovered inside its capacity contract: {_yes_no(recovery_verified)}",
            f"- GitOps reconciled controlled drift: {_yes_no(gitops_rollback_verified)}",
            "",
            "## Findings",
            "",
            (
                "The service rejected excess work instead of growing an unbounded "
                "queue. Dependency failures and deadlines remained explicit HTTP "
                "outcomes. Client concurrency, request count, and timeout prevented "
                "the exercise itself from becoming an unbounded workload."
            ),
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Render game-day evidence.")
    parser.add_argument("reports", nargs="+", type=Path)
    parser.add_argument("--alerts-verified", action="store_true")
    parser.add_argument("--recovery-verified", action="store_true")
    parser.add_argument("--gitops-rollback-verified", action="store_true")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    reports = [_read_report(path) for path in args.reports]
    rendered = render_report(
        reports,
        alerts_verified=args.alerts_verified,
        recovery_verified=args.recovery_verified,
        gitops_rollback_verified=args.gitops_rollback_verified,
    )
    asyncio.run(asyncio.to_thread(args.output.write_text, rendered))


def _read_report(path: Path) -> LoadReport:
    raw: Any = json.loads(path.read_text())
    return LoadReport.model_validate(raw)


def _yes_no(value: bool) -> str:
    return "verified" if value else "not verified"


if __name__ == "__main__":
    main()
