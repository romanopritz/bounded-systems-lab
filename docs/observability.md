# Bounded Observability

## Design

The observability stack is deliberately smaller than a general-purpose platform.
It deploys one Prometheus, one Alertmanager, and one Grafana replica in the
`observability` namespace. The components use immutable image digests, fixed
resource limits, default-deny networking, and no Kubernetes API credentials.

Prometheus discovers both workload replicas from a headless Service through DNS.
It scrapes every 30 seconds with a five-second timeout. Only Prometheus can cross
the namespace boundary, and only to port 8000 on pods carrying the workload label.
Grafana can query Prometheus, and Prometheus can send alerts to Alertmanager; no
other application data path is admitted.

The stack keeps at most 24 hours or 512 MB of Prometheus samples. Storage uses a
size-limited `emptyDir`, so a Prometheus replacement loses history. That tradeoff
is intentional for this single-node lab: bounded overhead and reproducible
recovery take precedence over durable telemetry. A production design would add a
tested persistent or remote-write destination with its own failure budget.

## Service Objectives

| Signal | Objective | Indicator |
| --- | --- | --- |
| Availability | 99.9% of scrape opportunities succeed | Mean `up` across discovered workload targets |
| Latency | 95% of completed work finishes within 1 second | Completed request histogram bucket at 1 second |
| Rejection | Fewer than 1% of requests are rejected | `rejected` outcomes divided by all outcomes |
| Errors | Fewer than 1% of requests fail or time out | `failed` and `timed_out` outcomes divided by all outcomes |
| Saturation | Operational warning at 80% for 10 minutes | Running plus queued work divided by configured capacity |

The dashboard shows five-minute indicators and one-hour remaining error budgets.
One-hour windows make the lab responsive enough to exercise. They are not a
substitute for a production SLO compliance window, which should follow the
service's user and release commitments.

## Capacity Budget

| Component | CPU request / limit | Memory request / limit | Ephemeral request / limit |
| --- | --- | --- | --- |
| Prometheus | 150m / 500m | 300 MiB / 512 MiB | 512 MiB / 1 GiB |
| Alertmanager | 25m / 100m | 32 MiB / 64 MiB | 32 MiB / 128 MiB |
| Grafana | 50m / 250m | 128 MiB / 256 MiB | 128 MiB / 384 MiB |
| Total | 225m / 850m | 460 MiB / 832 MiB | 672 MiB / 1.5 GiB |

The namespace quota is intentionally higher than the current total but still
prevents an accidental deployment or configuration change from consuming the
host. Prometheus query concurrency is capped at four and query runtime at 30
seconds. Grafana is provisioned as an anonymous Viewer and has no plugin or
administrative workflow in this deployment.

## Bootstrap And Verification

Create the namespace as an operator, then update the Argo CD application layer.
Argo CD owns every namespaced monitoring resource after bootstrap:

```bash
kubectl apply -f platform/observability/base/namespace.yaml
kubectl apply -k platform/gitops/application
kubectl get application bounded-observability -n argocd
kubectl get pods,service,networkpolicy -n observability
kubectl top pods -n observability --containers
kubectl get resourcequota observability-budget -n observability
```

Validate configuration and rules before deployment:

```bash
docker run --rm --entrypoint /bin/promtool \
  --volume "$PWD/platform/observability:/work:ro" \
  --workdir /work/tests \
  quay.io/prometheus/prometheus:v3.14.0@sha256:5ce7540c3c00ef4ab0c9d2c995c6a5b9c421f44b4a115d97a2c7af3b1c21cbb0 \
  test rules rules.test.yml
```

## Private UI Access

The services are cluster-internal. Bind a port-forward only to host loopback:

```bash
kubectl port-forward service/grafana 13000:3000 \
  --address 127.0.0.1 -n observability
```

Create an SSH tunnel from the workstation to the same loopback port, then open
`http://127.0.0.1:13000`. Anonymous access is read-only. Do not expose this port
through the host or edge firewall.

## Alert Runbooks

### Metrics Missing

Confirm the headless metrics Service has two endpoints, then inspect Prometheus
DNS resolution and both namespace network policies. Do not widen the policy to all
pods or namespaces to make the alert disappear.

### Target Down

Identify the failed target in Prometheus, compare it with ready workload pods, and
check probe failures and recent restarts. One target down removes half of the
service's fixed capacity even if the cluster Service still responds.

### Availability Burn

Check target health before changing application capacity. If both targets fail,
inspect the namespace policy and service endpoints; if one fails, inspect that
pod's events and logs. Stop load generation while the cause is unclear.

### Error Burn

Split `failed` from `timed_out` outcomes. Timeouts point to work duration or queue
delay; failures point to application execution. Preserve the deadline and reduce
admitted load before considering a larger timeout.

### High Rejection Rate

Check running, queued, and capacity series together. Rejection is the intended
overload behavior. Reduce offered load or add tested capacity; do not remove the
queue bound.

### Latency Burn

Compare p95 latency with saturation and timeouts. If queue occupancy is high,
reduce load first. If occupancy is low, inspect the work-duration distribution
and host contention.

### Sustained Saturation

Confirm the signal persists across both replicas and inspect host and monitoring
overhead. Keep a capacity reserve for recovery. Increasing concurrency without a
load test can move the bottleneck and make timeout behavior worse.
