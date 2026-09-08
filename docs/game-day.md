# Overload And Recovery Game Day

This exercise proves that overload, dependency faults, alerting, recovery, and
GitOps reconciliation behave as designed. Every load run has a fixed request
count, worker count, request timeout, and optional inter-request delay. The
generator does not emit its target URL into results.

## Safety Envelope

- Run against a disposable local container first.
- Keep the service and load generator on a private Docker network; publish no port.
- Set `LAB_ENABLE_FAULT_INJECTION=true` only on the disposable service.
- Use only the `normal` scenario against the normal GitOps deployment.
- Do not disable host or provider firewalls.
- Stop if client transport errors appear or node resource pressure is observed.
- Store only the generated JSON and report; inspect them for identifiers before commit.

## Containerized Local Exercise

Build the candidate image and create a private network:

```bash
docker build --tag bounded-systems-lab:game-day .
docker network create bounded-game-day
docker run --detach --rm --name bounded-game-service \
  --network bounded-game-day \
  --read-only --tmpfs /tmp:rw,noexec,nosuid,size=16m \
  --cap-drop ALL --security-opt no-new-privileges \
  --memory 256m --cpus 1 \
  --env LAB_ENABLE_FAULT_INJECTION=true \
  bounded-systems-lab:game-day
```

Run the four scenarios. The generator container is unprivileged and receives no
Docker socket, host mount, credentials, or Kubernetes token.

```bash
mkdir -p game-days/current/results

docker run --rm --network bounded-game-day bounded-systems-lab:game-day \
  python -m bounded_systems_lab.loadgen \
  --target-url http://bounded-game-service:8000 \
  --requests 100 --concurrency 12 --duration-ms 1500 \
  > game-days/current/results/saturation.json

docker run --rm --network bounded-game-day bounded-systems-lab:game-day \
  python -m bounded_systems_lab.loadgen \
  --target-url http://bounded-game-service:8000 \
  --scenario dependency_latency \
  --requests 4 --concurrency 1 --duration-ms 1500 \
  > game-days/current/results/dependency-latency.json

docker run --rm --network bounded-game-day bounded-systems-lab:game-day \
  python -m bounded_systems_lab.loadgen \
  --target-url http://bounded-game-service:8000 \
  --scenario dependency_failure \
  --requests 4 --concurrency 1 --duration-ms 0 \
  > game-days/current/results/dependency-failure.json

docker run --rm --network bounded-game-day bounded-systems-lab:game-day \
  python -m bounded_systems_lab.loadgen \
  --target-url http://bounded-game-service:8000 \
  --scenario dependency_timeout \
  --requests 4 --concurrency 1 --duration-ms 0 \
  > game-days/current/results/dependency-timeout.json
```

Expected outcomes are a mix of `200` and intentional `503` responses during
saturation, `200` for bounded latency, `502` for dependency failure, and `504`
for service deadline enforcement. A subsequent normal request must return `200`.

## Cluster Alert And Recovery

Use the immutable release image containing the load generator. Keep the service
private with a port forward, and run the generator on the cluster host so
`--network host` resolves loopback on that host:

```bash
kubectl port-forward -n bounded-lab service/bounded-systems-lab 18000:80

docker run --rm --network host <release-image-by-digest> \
  python -m bounded_systems_lab.loadgen \
  --target-url http://127.0.0.1:18000 \
  --requests 7200 --concurrency 24 --duration-ms 1500 \
  --inter-request-delay-ms 1000 \
  > game-days/current/results/cluster-saturation.json
```

The 7,200-request ceiling and delay sustain controlled pressure long enough for
the five-minute rejection alert without creating an unbounded client. During the
run, verify `BoundedLabHighRejectionRate` reaches `firing`; after the run, verify
it clears and a 0 ms normal request returns `200`. Record node pressure and pod
restart counts before and after.

## GitOps Reconciliation

Create controlled, reversible drift by changing only the live replica count from
two to one. Argo CD self-healing should restore the Git-declared count of two.
Verify both replicas become Ready and the application returns to `Synced` and
`Healthy`. Do not change images, credentials, storage, networking, or firewalls
for this check.

## Report

After independently verifying alerts, recovery, and reconciliation, render the
incident report:

```bash
uv run python -m bounded_systems_lab.game_day_report \
  game-days/current/results/*.json \
  --alerts-verified --recovery-verified --gitops-rollback-verified \
  --output game-days/current/incident-report.md
```

Retain the exact JSON. Do not mark a check verified based only on the command's
exit status; compare service metrics, Prometheus alert state, Argo CD state, and
workload readiness before recording the result.
