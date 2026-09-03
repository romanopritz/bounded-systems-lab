# Bounded Systems Lab

[![CI](https://github.com/romanopritz/bounded-systems-lab/actions/workflows/ci.yml/badge.svg)](https://github.com/romanopritz/bounded-systems-lab/actions/workflows/ci.yml)

Reference implementation for bounded asynchronous work and a hardened single-node
Kubernetes deployment.

The project demonstrates:

- explicit concurrency, queue, and deadline limits;
- load shedding instead of unbounded buffering;
- Prometheus metrics for outcomes, latency, saturation, and queue depth;
- a reproducible, non-root container;
- restricted Kubernetes workloads with quotas and default-deny networking;
- operational controls suitable for fixed-capacity environments.

## Reference Environment

- Ubuntu 26.04 LTS or another modern Linux distribution
- Python 3.14 with `uv`
- Docker or another OCI-compatible image builder
- K3s v1.36.4+k3s1 with a dedicated upstream `kubectl` client

## Capabilities

1. Built a Python service with explicit overload controls.
2. Added metrics for latency, saturation, queue depth, rejects, and failures.
3. Containerized it with pinned dependencies, a digest-pinned base, and runtime limits.
4. Deployed two restricted replicas to K3s with probes, quotas, and network policies.
5. Added namespace-scoped Argo CD reconciliation with bounded control-plane resources.
6. Added bounded Prometheus, Alertmanager, and Grafana resources with tested SLO rules.

## Current Service

The lab now exposes a small simulated workload over HTTP. Its capacity is explicit:
running work and waiting work are bounded, requests have an end-to-end deadline,
and excess work receives `503 Service Unavailable` with `Retry-After`.

Install the locked dependencies and start it as an unprivileged user:

```bash
uv sync --locked --dev
uv run uvicorn bounded_systems_lab.api:app \
  --host 127.0.0.1 --port 8000 --no-access-log
```

Useful endpoints:

- `POST /v1/work` with `{"duration_ms": 250}`
- `GET /healthz` and `GET /readyz`
- `GET /status` for a point-in-time capacity view
- `GET /metrics` for Prometheus metrics

Configuration is read from `LAB_MAX_CONCURRENCY`, `LAB_MAX_QUEUE_SIZE`, and
`LAB_WORK_TIMEOUT_SECONDS`. Health and metrics probes are intentionally omitted
from request logs to avoid turning routine scraping into avoidable log volume.

## Container

`requirements.lock` is generated from `uv.lock` and pins runtime dependencies
with package hashes. The container is based on a digest-pinned Python image and
runs as UID/GID `10001` without a login shell or writable home directory.

Run Docker only from an account explicitly authorized to access its socket. Docker
socket access is effectively host-level access and should not be granted casually.

```bash
docker build --pull --tag bounded-systems-lab:local .
docker run --rm --name bounded-systems-lab \
  --read-only --tmpfs /tmp:rw,noexec,nosuid,size=16m \
  --cap-drop ALL --security-opt no-new-privileges \
  --memory 256m --cpus 1 \
  --publish 127.0.0.1:18000:8000 \
  bounded-systems-lab:local
```

Run the tests with:

```bash
uv run pytest -q
```

## CI And Releases

Pull requests and pushes to `main` run formatting, lint, type, test, manifest,
container-build, and vulnerability checks without publishing an image. Publishing
a GitHub release builds the image, pushes it to
`ghcr.io/romanopritz/bounded-systems-lab`, and records provenance and SBOM
attestations. Workflow permissions are read-only by default; only the release job
receives package and attestation write access.

The published image is public. Pull release `v0.1.0` by immutable digest:

```bash
docker pull \
  ghcr.io/romanopritz/bounded-systems-lab@sha256:ebe9f897359cbe2aa5485b3ca2c49890a20ddb2cb8f344cdbaf6d8dde8621e24
```

## Kubernetes

The base manifests are in `platform/kubernetes/base`. They create a dedicated
`bounded-lab` namespace with restricted Pod Security admission, a resource quota,
default container limits, default-deny networking, and a cluster-internal service.
There is no ingress controller, load balancer, public application port, or mounted
Kubernetes API token.

Useful checks:

```bash
kubectl get pods,service,networkpolicy -n bounded-lab
kubectl rollout status deployment/bounded-systems-lab -n bounded-lab
kubectl port-forward service/bounded-systems-lab 18000:80 -n bounded-lab
```

The base intentionally contains no user or group RoleBindings. Access policy is
environment-specific and should be managed separately. Until a registry is
configured, image builds and imports into K3s containerd remain operator actions.
The local deployment uses `imagePullPolicy: Never` so a missing tag cannot fall
back to an unintended public image; a registry deployment should use a verified
digest. The `platform/kubernetes/overlays/ghcr` overlay pins the public `v0.1.0`
image by digest and changes the pull policy to `IfNotPresent`:

```bash
kubectl apply -k platform/kubernetes/overlays/ghcr
```

Argo CD configuration is in `platform/gitops`. The control plane is pinned to an
immutable Argo CD 3.5.2 commit, has a namespace quota and bounded reconciliation
parallelism, and uses namespace-scoped installation permissions. A dedicated
`AppProject` and per-namespace Kubernetes `Role` limit reconciliation to
`bounded-lab` and `observability`. See [docs/gitops.md](docs/gitops.md) for
bootstrap, verification, private UI access, and rollback procedures.

The monitoring manifests are in `platform/observability`. They provide explicit
scrape, retention, storage, query, CPU, and memory budgets; a narrow
cross-namespace scrape policy; tested SLO recording and alerting rules; and a
provisioned Grafana dashboard. See
[docs/observability.md](docs/observability.md) for objectives, capacity
assumptions, private access, and alert runbooks.

## License

Licensed under the Apache License, Version 2.0. See [LICENSE](LICENSE).
