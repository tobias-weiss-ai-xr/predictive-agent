#!/usr/bin/env bash
set -euo pipefail

# Create required directories
mkdir -p /var/lib/opendesk

# Export environment variables
export OPERATOR_VERSION=${OPERATOR_VERSION:-4.0.0}

# Set PYTHONPATH to ensure the predictive_agent package is discoverable
export PYTHONPATH=/predictive_agent:${PYTHONPATH}

# Log startup information
echo "Starting openDesk Predictive Agent v${OPERATOR_VERSION}"
echo "PYTHONPATH: ${PYTHONPATH}"
echo "LLM_BACKEND: ${LLM_BACKEND:-not set}"
echo "OLLAMA_URL: ${OLLAMA_URL:-not set}"
echo "OLLAMA_MODEL: ${OLLAMA_MODEL:-not set}"

# Execute the main module
exec python3 -m predictive_agent
