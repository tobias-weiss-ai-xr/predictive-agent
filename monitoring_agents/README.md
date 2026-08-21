# Ansible Role: monitoring_agents

Deploys Docker-based monitoring containers via Docker Compose with OPERATOR_WATCH_* environment variables.

## Role Variables

### Required Variables

- `monitoring_agents_compose_project`: Name of the Docker Compose project
- `monitoring_agents_containers`: List of containers to deploy

### OPERATOR_WATCH_* Environment Variables

These variables control which containers/pods are monitored by the predictive-agent when running in Docker mode:

- `OPERATOR_RUNTIME`: Container runtime - `docker` or `kubernetes` (default: auto-detected based on Docker socket)
- `OPERATOR_WATCH_LABELS`: Comma-separated list of label selectors (format: `key=value,key2=value2`)
  - Example: `"app=web,env=prod"`
  - Only containers with all specified labels will be monitored
- `OPERATOR_WATCH_COMPOSE_PROJECTS`: Comma-separated list of Docker Compose project names
  - Example: `"myapp,webapp,api"`
  - Only containers from these projects will be monitored
- `OPERATOR_WATCH_NAMES`: Comma-separated list of container name patterns (supports wildcards)
  - Example: `"web-*,api-*,db"`
  - Only containers matching these patterns will be monitored
- `OPERATOR_WATCH_NAMESPACES`: Comma-separated list of Kubernetes namespaces (Kubernetes mode only)
  - Example: `"opendesk,opendesk-edu,default,llm"`
  - Only pods in these namespaces will be monitored

### Default Values

If no OPERATOR_WATCH_* variables are set:
- **Docker mode**: All running containers are monitored
- **Kubernetes mode**: All pods in the configured namespaces are monitored

## Example Usage

### Inventory

```ini
[monitoring_hosts]
monitor01.example.com
monitor02.example.com
```

### Group Variables

```yaml
# group_vars/monitoring_hosts.yml
monitoring_agents_enabled: true
monitoring_agents_compose_project: "monitoring"

monitoring_agents_containers:
  - name: "predictive-agent"
    image: "predictive-agent:v4.0"
    environment:
      OPERATOR_RUNTIME: "docker"
      OPERATOR_WATCH_COMPOSE_PROJECTS: "webapp,api,database"
      OPERATOR_WATCH_LABELS: "app=critical"
      OPERATOR_WATCH_NAMES: "web-*,api-*"
      RECONCILE_INTERVAL: "60"
      PREDICTION_RISK_THRESHOLD: "0.5"
    volumes:
      - "/var/run/docker.sock:/var/run/docker.sock:ro"
      - "./state:/var/lib/opendesk"
    ports:
      - "8080:8080"
    restart: unless-stopped
```

### Running the Role

```bash
ansible-playbook -i inventory.ini deploy-monitoring.yml
```

## Docker Compose Integration

This role can deploy monitoring containers directly or manage existing Docker Compose deployments.

The predictive-agent will automatically detect Docker mode when:
1. `OPERATOR_RUNTIME=docker` is set explicitly, OR
2. Docker socket (`/var/run/docker.sock`) is available

### Selector Precedence

When multiple selectors are configured (labels, compose projects, names), a container must match ALL criteria to be monitored. If no selectors are configured, all containers are monitored.
