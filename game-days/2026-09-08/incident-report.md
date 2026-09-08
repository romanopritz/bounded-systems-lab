# Overload And Recovery Game Day

## Scope

Executed 6 bounded scenarios with fixed client request, concurrency, and timeout budgets. Targets and infrastructure identifiers are intentionally omitted from the evidence.

## Results

| Scenario | Attempts | Statuses | p95 | Maximum |
| --- | ---: | --- | ---: | ---: |
| dependency_failure | 4 | 502: 4 | 17.4 ms | 17.4 ms |
| dependency_latency | 4 | 200: 4 | 1518.2 ms | 1518.2 ms |
| dependency_timeout | 4 | 504: 4 | 2019.6 ms | 2019.6 ms |
| normal | 1 | 200: 1 | 18.0 ms | 18.0 ms |
| normal | 100 | 200: 2, 503: 94, 504: 4 | 1520.2 ms | 2020.8 ms |
| normal | 7200 | 200: 44, 503: 6002, 504: 1154 | 2004.1 ms | 2031.8 ms |

## Verification

- Alerts observed and cleared: verified
- Service recovered inside its capacity contract: verified
- GitOps reconciled controlled drift: verified

## Findings

The service rejected excess work instead of growing an unbounded queue. Dependency failures and deadlines remained explicit HTTP outcomes. Client concurrency, request count, and timeout prevented the exercise itself from becoming an unbounded workload.
