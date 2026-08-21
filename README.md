# openDesk Predictive Agent v4.0

**Predictive Kubernetes Health Monitor**

## Overview

Predictive-agent v4.0 shifts from reactive (detect crash → analyze) to predictive (detect trends → predict failure → warn before crash).

### Key Features

- **Kalman filter** (2D: level + velocity) for memory/CPU trend estimation with confidence intervals
- **Markov chain** (6 states) for pod state transition prediction
- **Bayesian risk scoring** combining 6 signals (memory %, trend, CPU %, restarts, logs, node pressure)
- **Enhanced LLM analysis** with predictions, uncertainty, and time-to-failure
- **New endpoints**: `/predictions`, `/state`, `/reanalyze`
- **State persistence**: Markov transitions and prediction tracking
- **kubectl top metrics** collection for all pods
- **Multiple LLM backends**: Ollama, Saia API, OpenAI-compatible

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                                                                             │
│   ┌─────────────┐    ┌─────────────┐    ┌─────────────┐    ┌─────────────┐  │
│   │  Collector  │    │  Kalman     │    │  Markov     │    │  Risk       │  │
│   │  (kubectl)  │───▶│  Filter     │───▶│  Chain      │───▶│  Predictor  │  │
│   └─────────────┘    └─────────────┘    └─────────────┘    └─────────────┘  │
│                                                                             │
│   ┌─────────────┐    ┌─────────────┐    ┌─────────────┐                   │
│   │  LLM        │    │  HTTP       │    │  Persistence│                   │
│   │  Analyzer   │◀───│  Server     │    │  (PVC)      │                   │
│   └─────────────┘    └─────────────┘    └─────────────┘                   │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Installation

### Prerequisites

- Kubernetes cluster with metrics-server
- Python 3.11+
- kubectl configured
- PVC for persistence (optional but recommended)

### Deployment

```bash
# Clone repository
git clone https://github.com/tobias-weiss-ai-xr/predictive-agent.git
cd predictive-agent

# Build image
docker build -t predictive-agent:v4.0 .

# Deploy to Kubernetes
kubectl apply -f k8s/deployment.yaml
kubectl apply -f k8s/service.yaml
kubectl apply -f k8s/rbac.yaml

# Or deploy with Docker Compose (see monitoring_agents Ansible role)
docker compose -f monitoring_agents/docker-compose.yml up -d
```

## Configuration

### Environment Variables

```env
# Core
OPERATOR_NAME=opendesk-predictive-agent
OPERATOR_NAMESPACE=opendesk-predictive-agent
OPERATOR_VERSION=4.0.0
WATCH_NAMESPACES=opendesk,opendesk-edu,default,llm

# Runtime Detection (Docker vs Kubernetes)
# - 'docker': Monitor Docker containers via Docker socket
# - 'kubernetes': Monitor Kubernetes pods via kubectl (default: auto-detected)
# Auto-detection: if DOCKER_SOCKET exists or /var/run/docker.sock is available, uses 'docker'
OPERATOR_RUNTIME=docker

# Docker Mode Selectors (used when OPERATOR_RUNTIME=docker or auto-detected as Docker)
# Filter which containers are monitored based on labels, compose projects, and names
# If all are empty, ALL running containers are monitored

# Comma-separated list of label selectors (format: key=value,key2=value2)
# Container must have ALL specified labels to be monitored
# Example: "app=web,env=prod,tier=backend"
OPERATOR_WATCH_LABELS=

# Comma-separated list of Docker Compose project names
# Only containers from these projects are monitored
# Example: "myapp,webapp,api,database"
OPERATOR_WATCH_COMPOSE_PROJECTS=

# Comma-separated list of container name patterns (supports wildcards: *, ?)
# Container name must match at least one pattern
# Example: "web-*,api-*,myapp-*"
OPERATOR_WATCH_NAMES=

# Kubernetes Mode Selectors (used when OPERATOR_RUNTIME=kubernetes or auto-detected as K8s)
# Comma-separated list of namespaces to monitor
# Example: "opendesk,opendesk-edu,default,llm"
OPERATOR_WATCH_NAMESPACES=default
```

### OPERATOR_WATCH_* Defaults and Behavior

| Variable | Default | Behavior |
|----------|---------|----------|
| `OPERATOR_RUNTIME` | Auto-detected | If `DOCKER_SOCKET` env var or `/var/run/docker.sock` exists, uses `docker`. Otherwise `kubernetes`. |
| `OPERATOR_WATCH_LABELS` | Empty (no filter) | If empty, labels are not used for filtering. Container must match ALL specified labels. |
| `OPERATOR_WATCH_COMPOSE_PROJECTS` | Empty (no filter) | If empty, all compose projects are monitored. Must match project label `com.docker.compose.project`. |
| `OPERATOR_WATCH_NAMES` | Empty (no filter) | If empty, all container names are monitored. Supports wildcard patterns (`*`, `?`). |
| `OPERATOR_WATCH_NAMESPACES` | `default` | In Kubernetes mode, only monitors pods in specified namespaces. |

**Selector Logic (Docker mode):**
- If ANY selector is configured (labels, compose projects, or names), a container must match ALL configured selectors to be monitored.
- If NO selectors are configured, ALL running containers are monitored.
- Wildcards in `OPERATOR_WATCH_NAMES` use shell-style pattern matching (e.g., `web-*` matches `web-1`, `web-2`, `web-frontend`).

**Example: Monitor only production web containers**
```env
OPERATOR_RUNTIME=docker
OPERATOR_WATCH_LABELS=app=web,env=prod
OPERATOR_WATCH_COMPOSE_PROJECTS=myapp
```

**Example: Monitor all containers except test environments**
```env
OPERATOR_RUNTIME=docker
OPERATOR_WATCH_LABELS=env=!test
```

# LLM (choose one backend)
LLM_BACKEND=ollama  # ollama, saia, openai
OLLAMA_URL=http://ollama.llm.svc.cluster.local:11434
OLLAMA_MODEL=qwen3-30b-a3b:latest
SAIA_API_URL=https://api.saia.ai/v1
SAIA_API_KEY=your-key
OPENAI_API_URL=https://api.openai.com/v1
OPENAI_API_KEY=your-key

# Reconcile
RECONCILE_INTERVAL=60
MAX_PODS_PER_CYCLE=3

# Prediction
PREDICTION_ENABLED=true
PREDICTION_RISK_THRESHOLD=0.5
KALMAN_PROCESS_NOISE=1.0
KALMAN_MEASUREMENT_NOISE=100.0

# Persistence
STATE_MODEL_FILE=/var/lib/opendesk/state-model.json
PREDICTIONS_FILE=/var/lib/opendesk/predictions.json
HISTORY_FILE=/var/lib/opendesk/analysis-history.json
```

## Endpoints

### Health

- `GET /healthz` - Liveness probe
- `GET /ready` - Readiness probe
- `GET /startup` - Startup probe

### Metrics

- `GET /metrics` - Prometheus metrics
- `GET /status` - Full status JSON

### Analysis

- `GET /history` - Analysis history
- `GET /cache` - Analysis cache
- `GET /reanalyze/{pod}` - Force re-analysis

### Predictive (v4.0)

- `GET /predictions` - All pods with risk > threshold
- `GET /state` - Markov chain + pod states

## Development

### Testing

```bash
# Run unit tests
python -m pytest tests/

# Run integration tests (requires k8s cluster)
python -m pytest tests/integration/

# Run linter
python -m pylint predictive_agent
```

### TDD Workflow

1. Write failing test
2. Watch it fail
3. Write minimal code to pass
4. Refactor
5. Repeat

## License

MIT
