# openDesk Dev Agent v4.0

**Predictive Kubernetes Health Monitor**

## Overview

Dev-agent v4.0 shifts from reactive (detect crash → analyze) to predictive (detect trends → predict failure → warn before crash).

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
git clone https://github.com/tobias-weiss-ai-xr/dev-agent.git
cd dev-agent

# Build image
docker build -t dev-agent:v4.0 .

# Deploy to Kubernetes
kubectl apply -f k8s/deployment.yaml
kubectl apply -f k8s/service.yaml
kubectl apply -f k8s/rbac.yaml
```

## Configuration

### Environment Variables

```env
# Core
OPERATOR_NAME=opendesk-dev-agent
OPERATOR_NAMESPACE=opendesk-dev-agent
OPERATOR_VERSION=4.0.0
WATCH_NAMESPACES=opendesk,opendesk-edu,default,llm

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
python -m pylint dev_agent.py
```

### TDD Workflow

1. Write failing test
2. Watch it fail
3. Write minimal code to pass
4. Refactor
5. Repeat

## License

MIT
