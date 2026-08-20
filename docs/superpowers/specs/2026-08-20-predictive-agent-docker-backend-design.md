# Predictive-Agent Docker Backend — Design Spec

**Date:** 2026-08-20
**Repo:** `tobias-weiss-ai-xr/predictive-agent` (branch `nix`)
**Status:** Approved design, awaiting implementation plan

## 1. Background & Problem

`predictive-agent` v4.0 is a Kubernetes health operator: `collector.py` and all
`actions/*` shell out to `kubectl` (`top`, `get`, `logs`), and the state model
tracks pods keyed `namespace/name`. It is already deployed to a Kubernetes
cluster (see `k8s/deployment.yaml`).

We want each Docker host (starting with **legion**) to run its own
predictive-agent that monitors **local Docker containers** through the read-write
Docker socket, reusing the entire Kalman-filter / Markov-chain / Bayesian-risk
pipeline unchanged. The kubectl path must remain fully intact for the SCS
bare-metal k3s deployment.

## 2. Decisions (confirmed with operator)

1. **Build:** single Nix image (`nix/predictive-agent.nix` style). No plain
   Dockerfile. The Docker socket client is stdlib-only (urllib over unix socket),
   so no extra binaries are required in the image.
2. **LLM backend:** agent on legion calls **deepseek-v4-flash on ai1 directly**,
   not the local LiteLLM proxy:
   - `OPENAI_API_URL=http://192.168.0.27:8888/v1` (ai1 DSpark, LAN)
   - `OPENAI_MODEL=deepseek-v4-flash-0731`
   - `OPENAI_API_KEY=not-needed`
3. **K8s path intact:** kubectl collector, kubectl remediation actions, and the
   `k8s/` manifests are unchanged. Backend selection via new `COLLECTOR_MODE`
   env var (`kubectl` default, `docker` for socket mode).

## 3. Architecture

```
main.py
  ├─ collector dispatch on config.COLLECTOR_MODE
  │    ├─ "kubectl"  → existing collector.py (unchanged)
  │    └─ "docker"   → NEW collector_docker.py (Docker Engine API over /var/run/docker.sock)
  │                     └─ records normalized to the same shape state_model consumes
  ├─ StateModel / Predictor / Markov / Risk  (shared, unchanged)
  ├─ server.py / persistence / notifier       (shared, unchanged)
  └─ remediation manager
       ├─ kubectl mode → existing actions (unchanged)
       └─ docker mode  → NEW actions/container_restart.py only
```

The shared pipeline knows nothing about Docker. The docker collector converts
docker containers into `ns/name` records where the **namespace** is the
`com.docker.compose.project` label (fallback: first path component of the
container name or `default`).

## 4. Component: `predictive_agent/collector_docker.py`

Stdlib HTTP client over a unix socket (`socket.socket(AF_UNIX)` + `http.client`).
No third-party deps, mirrors the `llm.py` urllib style. Methods:

| Docker Engine API | Purpose |
|---|---|
| `GET /containers/json?all=1` | list containers; filter to watched namespaces by `com.docker.compose.project` label |
| `GET /containers/{id}/json` | memory limit (`HostConfig.Memory`), `RestartCount`, running state |
| `GET /containers/{id}/stats?stream=false` | CPU (`cpu_stats`/`precpu_stats`) and memory (`memory_stats`) usage |
| `GET /containers/{id}/logs?tail=50&stderr=1` | tail for `count_log_errors` error scan |
| `GET /info` | host memory/cpu totals for node-pressure approximation |

Normalized record per container (consumed by `state_model.update_pod`):
`namespace, name, memory_mib, memory_limit_mib, cpu_m, restart_count,
log_errors, node_pressure`.

- `node_pressure` = host memory under pressure (host memory usage ratio over
  threshold from `/info`, no k8s node conditions).
- Uses `config.WATCH_NAMESPACES` for filtering (legion:
  `opendesk,opendesk-sme,monitoring,litellm,hubs`).
- Socket path configurable via `DOCKER_SOCKET` env (default
  `/var/run/docker.sock`).

## 5. Component: `predictive_agent/actions/container_restart.py`

New `RemediationAction`:
- `name = "container_restart"`
- `should_execute`: risk_score > `REMEDIATION_RISK_THRESHOLD`, restart_count >= 5
  or container not running, and namespace not in `REMEDIATION_PROTECTED_NS`.
- `execute`: `POST /containers/{id}/restart` via the socket client; honors
  `context.dry_run` (no-op with success message). Human-readable command string
  e.g. `docker restart <name>` for logs.

Kubectl-only actions (`pod_restart`, `node_cordon`, `scale`, `rollout_restart`,
`right_size`, `tune_resources`) are only registered in kubectl mode.

## 6. Component: main.py wiring

- `config.py`: add `COLLECTOR_MODE` (default `kubectl`), `DOCKER_SOCKET`
  (default `/var/run/docker.sock`), `DOCKER_HOST_PRESSURE_THRESHOLD`
  (default 0.9).
- `main.py reconcile()`: branch statistics collection on `COLLECTOR_MODE`:
  kubectl path unchanged; docker path returns the normalized container records.
- `main.py main()`: register `ContainerRestartAction` only in docker mode;
  kubectl actions only in kubectl mode.

## 7. Deployment (legion first)

Via existing `monitoring_agents` ansible role, but legion-only and predictive-only:

- `- /var/run/docker.sock:/var/run/docker.sock` (**rw**, no `:ro`)
- `COLLECTOR_MODE=docker`
- `OPENAI_API_URL=http://192.168.0.27:8888/v1`
- `OPENAI_MODEL=deepseek-v4-flash-0731`
- `OPENAI_API_KEY=not-needed`
- `OPERATOR_WATCH_NAMESPACES=monitoring,litellm,hubs` (compose projects present on legion)
- State volume `predictive-agent-state:/var/lib/opendesk`
- Healthcheck `curl -fs http://localhost:8081/healthz`, restart `unless-stopped`
- Ports 8080 (metrics) / 8081 (health) on host
- `dev_agent_enabled: false`, `taskfleet_enabled: false` on legion
- Image tag `registry.chemie-lernen.org/predictive-agent:latest` (pulled from
  Zot registry on legion)

## 8. Image build (Nix)

Extend `nix/predictive-agent.nix`: keep layout, add `COLLECTOR_MODE=kubectl`
default env. No binary changes needed (socket client is stdlib). Build/push:
`predictive-agent:v8.3-docker` then tag `:latest` on the Zot registry
(`registry.chemie-lernen.org`). Image must continue to contain `curl`, `bash`,
`coreutils`, `python3` for entrypoint + healthcheck.

## 9. Error handling

- Socket unavailable / permission denied → log once, reconcile yields 0 tracked,
  server still serves healthz/ready. No crash loop on collector failure.
- Stats request partially failing for one container → that container keeps prior
  tracked state; other containers still processed.
- Malformed docker API JSON → treated like empty results (mirrors kubectl path).
- `dry_run` always honored for remediation; `REMEDIATION_ENABLED` gates actions.

## 10. Testing

- `tests/test_collector_docker.py`: start an in-process unix-socket HTTP server
  serving canned docker API responses; assert parsing/filtering/normalization.
- `tests/test_container_restart.py`: restart action honors dry_run, thresholds,
  protected namespaces, and issues correct REST call.
- Existing kubectl-path tests must stay green (no behavior change).
- Run: `python -m pytest tests/`.

## 11. Out of scope (this iteration)

- Other hosts beyond legion (rollout after proven stable).
- dev-agent / taskfleet on legion (disabled).
- Docker `compose scale`/`update` remediation actions (restart only for now).
- Changing the k8s deployment manifests.