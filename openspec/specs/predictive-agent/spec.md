## Purpose
Predictive health-monitoring operator for Kubernetes and Docker that shifts from reactive (detect crash → analyze) to predictive (detect trends → predict failure → warn before crash).

## Requirements

### Requirement: Reconcile loop
The operator runs a periodic reconcile loop driven by `RECONCILE_INTERVAL` (default 60s) that collects metrics, updates the state model, runs risk prediction, and persists state.

#### Scenario: Periodic reconcile
- **WHEN** the operator is running and `RECONCILE_INTERVAL` elapses
- **THEN** it collects current metrics, updates the Kalman/Markov state, computes risk predictions, and writes state + predictions to the persistence files

### Requirement: Metrics collection
The operator collects workload metrics from the runtime: Kubernetes (via `kubectl top`/`get`/`logs`) or Docker (read-only Docker socket), scoped to `OPERATOR_WATCH_NAMESPACES`.

#### Scenario: Docker mode
- **WHEN** deployed as a Docker container with the Docker socket mounted read-only
- **THEN** it inspects containers in the watched namespaces and tracks their state

#### Scenario: Kubernetes mode
- **WHEN** deployed in a cluster with kubeconfig access
- **THEN** it uses `kubectl` to collect pod/node metrics and logs

### Requirement: State model (trend + transitions)
The operator maintains a state model combining a 2D Kalman filter (level + velocity) for memory/CPU trend estimation and a 6-state Markov chain for pod state-transition prediction.

#### Scenario: Trend estimation
- **WHEN** new metric samples arrive
- **THEN** the Kalman filter updates level/velocity estimates with confidence intervals

#### Scenario: State-transition prediction
- **WHEN** the Markov chain is updated with observed state changes
- **THEN** it predicts the next-state distribution for each tracked pod

### Requirement: Bayesian risk prediction
The operator computes a risk score combining six signals (memory %, trend, CPU %, restarts, logs, node pressure) using Bayesian scoring, producing predictions with uncertainty and time-to-failure.

#### Scenario: Risk scoring
- **WHEN** a pod is tracked
- **THEN** a risk score in [0,1] is produced and exposed via the predictions API

### Requirement: LLM analysis
The operator can call an LLM backend (Ollama, Saia API, or OpenAI-compatible/LiteLLM) for enhanced analysis with predictions, uncertainty, and time-to-failure.

#### Scenario: Multi-backend analysis
- **WHEN** `LLM_BACKEND` is configured
- **THEN** analysis requests are routed to the selected backend with its configured model

### Requirement: HTTP server (metrics + health)
The operator exposes an HTTP server with a metrics/API port (default 8080) and a health-probe port (default 8081).

#### Scenario: Metrics and API
- **WHEN** a client requests an API endpoint
- **THEN** it returns `/metrics` (Prometheus), `/status`, `/predictions`, `/state`, `/history`, `/reanalyze`, and `/cache`

#### Scenario: Health probe
- **WHEN** a health check queries the probe port
- **THEN** `/healthz` returns 200 (liveness) and `/ready` returns 200 (readiness)

### Requirement: State persistence
The operator persists the Markov transition matrix and prediction tracking to files on the state volume (`STATE_MODEL_FILE`, `PREDICTIONS_FILE`) so state survives restarts.

#### Scenario: Restart survival
- **WHEN** the operator restarts
- **THEN** it reloads the persisted state model and predictions

### Requirement: Packaging and deployment
The operator is built as a minimal nix image (Python 3 + kubectl + curl) and deployed either as a Docker Compose service (Zot registry `registry.chemie-lernen.org/predictive-agent:latest`) or as a Kubernetes Deployment with RBAC (ServiceAccount `opendesk-predictive-agent`).

#### Scenario: Docker deployment
- **WHEN** deployed via the `monitoring_agents` Ansible role
- **THEN** the container runs with the Docker socket mounted read-only and watches the configured namespaces

#### Scenario: Kubernetes deployment
- **WHEN** deployed via the k8s manifests
- **THEN** it runs with the `opendesk-predictive-agent` ServiceAccount/ClusterRole and reads cluster metrics
