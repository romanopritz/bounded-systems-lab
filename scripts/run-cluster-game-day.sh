#!/usr/bin/env bash

set -euo pipefail

readonly IMAGE="${IMAGE:?set IMAGE to an immutable image reference}"
readonly RESULTS_DIR="${RESULTS_DIR:-game-days/current/results}"
readonly REQUESTS="${REQUESTS:-7200}"
readonly CONCURRENCY="${CONCURRENCY:-24}"
readonly INTER_REQUEST_DELAY_MS="${INTER_REQUEST_DELAY_MS:-1000}"
readonly ALERT_TIMEOUT_SECONDS="${ALERT_TIMEOUT_SECONDS:-600}"
readonly SERVICE_PORT="18000"
readonly PROMETHEUS_PORT="19090"

if ((REQUESTS < 1 || REQUESTS > 10000)); then
  printf 'REQUESTS must be between 1 and 10000\n' >&2
  exit 2
fi
if ((CONCURRENCY < 1 || CONCURRENCY > 100)); then
  printf 'CONCURRENCY must be between 1 and 100\n' >&2
  exit 2
fi
if [[ "${CONFIRM_CONTROLLED_DRIFT:-no}" != "yes" ]]; then
  printf 'set CONFIRM_CONTROLLED_DRIFT=yes to permit the replica reconciliation check\n' >&2
  exit 2
fi

for command in curl docker kubectl python3; do
  command -v "$command" >/dev/null
done

mkdir -p "$RESULTS_DIR"
work_dir="$(mktemp -d)"
service_forward_pid=""
prometheus_forward_pid=""
load_pid=""

cleanup() {
  for pid in "$load_pid" "$service_forward_pid" "$prometheus_forward_pid"; do
    if [[ -n "$pid" ]]; then
      kill "$pid" 2>/dev/null || true
    fi
  done
  rm -rf "$work_dir"
}
trap cleanup EXIT

pod_restart_total() {
  kubectl get pods -n bounded-lab \
    -l app.kubernetes.io/name=bounded-systems-lab -o json |
    python3 -c 'import json,sys; print(sum(s.get("restartCount", 0) for p in json.load(sys.stdin)["items"] for s in p.get("status", {}).get("containerStatuses", [])))'
}

alert_state() {
  curl --fail --silent --show-error \
    "http://127.0.0.1:${PROMETHEUS_PORT}/api/v1/alerts" |
    python3 -c 'import json,sys; print(next((a["state"] for a in json.load(sys.stdin)["data"]["alerts"] if a.get("labels", {}).get("alertname") == "BoundedLabHighRejectionRate"), "inactive"))'
}

wait_for_http() {
  local url="$1"
  for _ in {1..30}; do
    if curl --fail --silent --output /dev/null "$url"; then
      return 0
    fi
    sleep 1
  done
  return 1
}

restarts_before="$(pod_restart_total)"
kubectl port-forward --address 127.0.0.1 -n bounded-lab \
  service/bounded-systems-lab "${SERVICE_PORT}:80" >"$work_dir/service-forward.log" 2>&1 &
service_forward_pid="$!"
kubectl port-forward --address 127.0.0.1 -n observability \
  service/prometheus "${PROMETHEUS_PORT}:9090" >"$work_dir/prometheus-forward.log" 2>&1 &
prometheus_forward_pid="$!"
wait_for_http "http://127.0.0.1:${SERVICE_PORT}/readyz"
wait_for_http "http://127.0.0.1:${PROMETHEUS_PORT}/-/ready"

docker run --rm --network host "$IMAGE" \
  python -m bounded_systems_lab.loadgen \
  --target-url "http://127.0.0.1:${SERVICE_PORT}" \
  --requests "$REQUESTS" \
  --concurrency "$CONCURRENCY" \
  --duration-ms 1500 \
  --inter-request-delay-ms "$INTER_REQUEST_DELAY_MS" \
  >"$RESULTS_DIR/cluster-saturation.json" &
load_pid="$!"

alert_observed=false
while kill -0 "$load_pid" 2>/dev/null; do
  state="$(alert_state)"
  printf 'load active; rejection alert state=%s\n' "$state"
  if [[ "$state" == "firing" ]]; then
    alert_observed=true
  fi
  sleep 30
done
wait "$load_pid"
load_pid=""

recovery_status="$(curl --silent --output /dev/null --write-out '%{http_code}' \
  --header 'Content-Type: application/json' \
  --data '{"duration_ms":0}' \
  "http://127.0.0.1:${SERVICE_PORT}/v1/work")"
recovery_verified=false
if [[ "$recovery_status" == "200" ]]; then
  recovery_verified=true
fi

alert_cleared=false
deadline="$((SECONDS + ALERT_TIMEOUT_SECONDS))"
while ((SECONDS < deadline)); do
  state="$(alert_state)"
  printf 'recovery active; rejection alert state=%s\n' "$state"
  if [[ "$state" == "inactive" ]]; then
    alert_cleared=true
    break
  fi
  sleep 30
done

initial_replicas="$(kubectl get deployment bounded-systems-lab -n bounded-lab \
  -o jsonpath='{.spec.replicas}')"
if [[ "$initial_replicas" != "2" ]]; then
  printf 'expected the Git-declared replica count to be 2, found %s\n' "$initial_replicas" >&2
  exit 1
fi
kubectl scale deployment bounded-systems-lab -n bounded-lab --replicas=1 >/dev/null
drift_observed=false
if [[ "$(kubectl get deployment bounded-systems-lab -n bounded-lab -o jsonpath='{.spec.replicas}')" == "1" ]]; then
  drift_observed=true
fi

gitops_rollback_verified=false
for _ in {1..60}; do
  desired="$(kubectl get deployment bounded-systems-lab -n bounded-lab -o jsonpath='{.spec.replicas}')"
  available="$(kubectl get deployment bounded-systems-lab -n bounded-lab -o jsonpath='{.status.availableReplicas}')"
  if [[ "$desired" == "2" && "$available" == "2" ]]; then
    gitops_rollback_verified=true
    break
  fi
  sleep 5
done

argo_synced=false
argo_healthy=false
if [[ "$(kubectl get application bounded-systems-lab -n argocd -o jsonpath='{.status.sync.status}')" == "Synced" ]]; then
  argo_synced=true
fi
if [[ "$(kubectl get application bounded-systems-lab -n argocd -o jsonpath='{.status.health.status}')" == "Healthy" ]]; then
  argo_healthy=true
fi
restarts_after="$(pod_restart_total)"

python3 - \
  "$alert_observed" "$alert_cleared" "$recovery_verified" \
  "$drift_observed" "$gitops_rollback_verified" "$argo_synced" "$argo_healthy" \
  "$restarts_before" "$restarts_after" >"$RESULTS_DIR/operational-checks.json" <<'PY'
import json
import sys

values = [value == "true" for value in sys.argv[1:8]]
document = {
    "schema_version": 1,
    "rejection_alert_observed": values[0],
    "rejection_alert_cleared": values[1],
    "recovery_verified": values[2],
    "controlled_drift_observed": values[3],
    "gitops_rollback_verified": values[4],
    "gitops_synced": values[5],
    "gitops_healthy": values[6],
    "pod_restarts_before": int(sys.argv[8]),
    "pod_restarts_after": int(sys.argv[9]),
}
json.dump(document, sys.stdout, indent=2, sort_keys=True)
print()
PY

if [[ "$alert_observed" != true || "$alert_cleared" != true || \
      "$recovery_verified" != true || "$drift_observed" != true || \
      "$gitops_rollback_verified" != true || "$argo_synced" != true || \
      "$argo_healthy" != true || "$restarts_before" != "$restarts_after" ]]; then
  printf 'one or more game-day checks failed; inspect anonymized evidence\n' >&2
  exit 1
fi

printf 'all bounded cluster game-day checks passed\n'
