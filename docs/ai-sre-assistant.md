# Read-Only AI SRE Assistant

The assistant is an operator-side, one-shot CLI. It combines an
OpenAI-compatible Chat Completions provider with three typed, read-only tools:

- `service_status` reads the service's bounded capacity counters;
- `slo_indicator` maps an enum to one of eight fixed Prometheus recording rules;
- `active_alerts` returns at most 20 alerts and strips labels that are not needed.

The model cannot supply a URL, raw PromQL expression, shell command, file path, or
Kubernetes object. Unknown tool names are rejected without execution. Tool
responses and user text are treated as untrusted context.

## Resource Contract

Default limits are deliberately small:

| Resource | Limit |
| --- | ---: |
| Concurrent runs | 1 |
| Waiting runs | 2 |
| End-to-end deadline | 25 seconds |
| Model timeout | 10 seconds |
| Model retries | 1 |
| Agent iterations | 3 |
| Tool calls | 4 |
| Output tokens per model call | 600 |
| Question / total context | 2,000 / 16,000 characters |
| Tool arguments / result | 2,000 / 4,000 characters |

The CLI emits a structured answer with model calls, tool calls, rejected calls,
retries, token use, iterations, and total duration. `--metrics-file` additionally
writes Prometheus counters and histograms for run, model, and tool outcomes and
latencies. Metric labels come only from bounded internal enums and allowlisted
tool names.

## Run Locally

Keep both data sources private and reach them with local port forwards:

```bash
kubectl port-forward -n bounded-lab service/bounded-systems-lab 18000:80
kubectl port-forward -n observability service/prometheus 19090:9090
```

In another shell, inject the provider configuration at runtime:

```bash
export LAB_AI_BASE_URL=https://api.openai.com/v1
export LAB_AI_MODEL=<compatible-model-name>
read -rsp 'API key: ' LAB_AI_API_KEY && export LAB_AI_API_KEY

uv run bounded-sre \
  --metrics-file=/tmp/bounded-sre.prom \
  'Is the service saturated, and are any SLO alerts firing?'
unset LAB_AI_API_KEY
```

`LAB_AI_TOKEN_PARAMETER=max_tokens` can be set for compatible servers that do not
accept `max_completion_tokens`. `LAB_SERVICE_URL` and `LAB_PROMETHEUS_URL` can
override the local defaults, but they are operator configuration and cannot be
changed by the model.

Do not put an API key in Git, Kubernetes manifests, shell history, or a committed
environment file. For unattended use, inject it from the platform's secret
manager and keep the model process outside the cluster's trust boundary. The
assistant has no Kubernetes credentials and no state-changing tools.

## Evaluations

`evals/assistant_cases.json` contains deterministic provider transcripts. The test
suite verifies ordinary evidence retrieval and a prompt-injection attempt that
asks for an undeclared write tool. These tests establish tool-boundary behavior;
they do not establish the semantic quality of a real model. Real-provider
evaluation should be opt-in, use a fixed prompt set and model version, and record
accuracy, abstention, tool selection, latency, tokens, and cost without recording
secrets.
