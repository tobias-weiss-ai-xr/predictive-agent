#!/usr/bin/env bash
set -euo pipefail

# Define the host and port
HOST=${HOST:-localhost}
PORT=${PORT:-8081}

# Function to check a specific endpoint
check_endpoint() {
  local endpoint="$1"
  local url="http://${HOST}:${PORT}${endpoint}"
  
  # Use curl to check the endpoint
  if curl -sSf -o /dev/null -w "%{http_code}" "$url" | grep -q "200"; then
    echo "${endpoint} is healthy"
    return 0
  else
    echo "${endpoint} is NOT healthy"
    return 1
  fi
}

# Check both required endpoints
if check_endpoint "/healthz" && check_endpoint "/ready"; then
  echo "All health checks passed"
  exit 0
else
  echo "Health checks failed"
  exit 1
fi
