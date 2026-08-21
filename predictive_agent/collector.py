"""Metrics collection from kubectl (top pods, get pods, logs, node conditions)."""

import json
import re
import subprocess

# Pre-compiled regex for error detection (much faster than 10 separate re.search calls)
_ERROR_RE = re.compile(
    r"\b(?:ERROR|Error|FATAL|PANIC|OOM|CrashLoopBackOff|Exception|Traceback)\b"
    r"|\bpanic:|\bfatal:"
)


def run_cmd(cmd, timeout=30):
    """Run a command and return (returncode, stdout, stderr)."""
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except Exception as e:
        return 1, "", str(e)


def parse_cpu(value):
    """Parse CPU string to millicores. '100m' → 100, '1.5' → 1500."""
    value = value.strip()
    if value.endswith("m"):
        return int(value[:-1])
    try:
        return int(float(value) * 1000)
    except ValueError:
        return 0


def parse_memory(value):
    """Parse memory string to MiB. '128Mi' → 128, '2Gi' → 2048."""
    value = value.strip()
    if value.endswith("Mi"):
        return int(value[:-2])
    elif value.endswith("Gi"):
        return int(value[:-2]) * 1024
    elif value.endswith("Ki"):
        return int(value[:-2]) // 1024
    elif value.endswith("Ti"):
        return int(value[:-2]) * 1024 * 1024
    try:
        return int(value) // (1024 * 1024)  # bytes to MiB
    except ValueError:
        return 0


def collect_top_metrics(output):
    """Parse `kubectl top pods -A` output. Returns {ns/name: {cpu_m, memory_mib}}."""
    metrics = {}
    lines = output.strip().split("\n")
    for line in lines[1:]:  # skip header
        parts = line.split()
        if len(parts) < 4:
            continue
        ns = parts[0]
        name = parts[1]
        cpu = parse_cpu(parts[2])
        mem = parse_memory(parts[3])
        metrics[f"{ns}/{name}"] = {"cpu_m": cpu, "memory_mib": mem}
    return metrics


def collect_top_nodes(output):
    """Parse `kubectl top nodes` output. Returns {node_name: {cpu_m, memory_mib, cpu_pct, memory_pct}}."""
    metrics = {}
    lines = output.strip().split("\n")
    for line in lines[1:]:
        parts = line.split()
        if len(parts) < 5:
            continue
        name = parts[0]
        cpu = parse_cpu(parts[1])
        mem = parse_memory(parts[3])
        cpu_pct = float(parts[2].rstrip("%")) if parts[2].endswith("%") else 0.0
        mem_pct = float(parts[4].rstrip("%")) if parts[4].endswith("%") else 0.0
        metrics[name] = {
            "cpu_m": cpu,
            "memory_mib": mem,
            "cpu_pct": cpu_pct,
            "memory_pct": mem_pct,
        }
    return metrics


def get_pod_resources(pod_json):
    """Extract resource limits from pod JSON. Returns {container_name: {cpu_m, memory_mib}}."""
    resources = {}
    spec = pod_json.get("spec", {})
    for container in spec.get("containers", []):
        name = container.get("name", "")
        limits = container.get("resources", {}).get("limits", {})
        cpu_limit = 0
        mem_limit = 0
        if "cpu" in limits:
            cpu_limit = parse_cpu(limits["cpu"])
        if "memory" in limits:
            mem_limit = parse_memory(limits["memory"])
        resources[name] = {"cpu_m": cpu_limit, "memory_mib": mem_limit}
    return resources


def get_node_conditions(node_json):
    """Extract node conditions from `kubectl get nodes -o json`."""
    conditions = {}
    for item in node_json.get("items", []):
        name = item.get("metadata", {}).get("name", "")
        for cond in item.get("status", {}).get("conditions", []):
            cond_type = cond.get("type", "")
            status = cond.get("status", "Unknown")
            conditions[name] = conditions.get(name, {})
            conditions[name][cond_type] = status
    return conditions


def count_log_errors(log_text):
    """Count error-level log lines."""
    count = 0
    for line in log_text.split("\n"):
        if _ERROR_RE.search(line):
            count += 1
    return count


# Container statuses that indicate serious problems
_CRASH_STATES = {
    "CrashLoopBackOff", "CreateContainerConfigError", "CreateContainerError",
    "ImagePullBackOff", "ErrImagePull", "InvalidImageName",
    "RunContainerError", "ContainerStatusUnknown",
}


# ─── Docker Runtime Detection and Selector Parsing ──────────────────────────

import os as _os

_RUNTIME_CACHE = None


def detect_runtime():
    """Auto-detect the container runtime (Docker or Kubernetes).
    
    Checks in order:
    1. OPERATOR_RUNTIME env var (explicit override)
    2. DOCKER_SOCKET env var pointing to a valid socket
    3. Default Docker socket at /var/run/docker.sock
    4. Default to 'kubernetes'
    
    Returns:
        'docker' or 'kubernetes'
    """
    global _RUNTIME_CACHE
    
    # Return cached value if available
    if _RUNTIME_CACHE is not None:
        return _RUNTIME_CACHE
    
    # Check explicit runtime setting
    runtime = _os.environ.get("OPERATOR_RUNTIME", "").lower()
    if runtime in ("docker", "kubernetes", "k8s"):
        if runtime == "k8s":
            _RUNTIME_CACHE = "kubernetes"
        else:
            _RUNTIME_CACHE = runtime
        return _RUNTIME_CACHE
    
    # Check for Docker socket
    docker_socket = _os.environ.get("DOCKER_SOCKET", "/var/run/docker.sock")
    if _os.path.exists(docker_socket):
        _RUNTIME_CACHE = "docker"
        return _RUNTIME_CACHE
    
    # Default to Kubernetes
    _RUNTIME_CACHE = "kubernetes"
    return _RUNTIME_CACHE


def parse_watch_labels(labels_str):
    """Parse OPERATOR_WATCH_LABELS environment variable.
    
    Format: comma-separated list of key=value pairs
    Example: "app=web,env=prod,version=1.0"
    
    Args:
        labels_str: String of comma-separated labels, or None
        
    Returns:
        dict: {key: value} or empty dict if None/empty
    """
    if not labels_str or not labels_str.strip():
        return {}
    
    labels = {}
    for pair in labels_str.split(","):
        pair = pair.strip()
        if not pair:
            continue
        # Split on first '=' to handle values that might contain '='
        if "=" in pair:
            key, value = pair.split("=", 1)
            labels[key.strip()] = value.strip()
    return labels


def parse_watch_compose_projects(projects_str):
    """Parse OPERATOR_WATCH_COMPOSE_PROJECTS environment variable.
    
    Format: comma-separated list of compose project names
    Example: "myapp,webapp,api"
    
    Args:
        projects_str: String of comma-separated project names, or None
        
    Returns:
        list: list of project names or empty list if None/empty
    """
    if not projects_str or not projects_str.strip():
        return []
    
    projects = []
    for project in projects_str.split(","):
        project = project.strip()
        if project:
            projects.append(project)
    return projects


def parse_watch_names(names_str):
    """Parse OPERATOR_WATCH_NAMES environment variable.
    
    Format: comma-separated list of container name patterns (supports wildcards)
    Example: "web-*,api-*,db"
    
    Args:
        names_str: String of comma-separated name patterns, or None
        
    Returns:
        list: list of name patterns or empty list if None/empty
    """
    if not names_str or not names_str.strip():
        return []
    
    names = []
    for name in names_str.split(","):
        name = name.strip()
        if name:
            names.append(name)
    return names


def get_watch_selectors():
    """Get all watch selectors based on the detected runtime.
    
    Returns:
        dict: Dictionary containing:
            - runtime: 'docker' or 'kubernetes'
            - labels: dict of label selectors (Docker mode only)
            - compose_projects: list of compose project names (Docker mode only)
            - names: list of name patterns (Docker mode only)
            - namespaces: list of K8s namespaces (Kubernetes mode only, from config)
    """
    runtime = detect_runtime()
    
    if runtime == "docker":
        return {
            "runtime": "docker",
            "labels": parse_watch_labels(_os.environ.get("OPERATOR_WATCH_LABELS")),
            "compose_projects": parse_watch_compose_projects(
                _os.environ.get("OPERATOR_WATCH_COMPOSE_PROJECTS")
            ),
            "names": parse_watch_names(_os.environ.get("OPERATOR_WATCH_NAMES")),
        }
    else:
        # Kubernetes mode - read from environment directly to avoid caching
        namespaces_str = _os.environ.get("OPERATOR_WATCH_NAMESPACES", "")
        namespaces = namespaces_str.split(",") if namespaces_str else []
        return {
            "runtime": "kubernetes",
            "namespaces": namespaces,
        }


def get_pod_status_signals(pod_json):
    """Extract pod phase and container status signals from pod JSON.

    Returns a dict with:
        pod_phase: str (Pending, Running, Succeeded, Failed, Unknown)
        container_ready: bool (True if main container is ready)
        wait_state: str or None (reason if container is waiting, e.g. CrashLoopBackOff)
        terminated: bool (True if container was terminated)
        terminated_reason: str or None (e.g. OOMKilled, Error)
        restart_count: int (total restarts across all containers)
        pod_initialized: bool
        pod_scheduled: bool
    """
    status = pod_json.get("status", {})
    spec = pod_json.get("spec", {})

    pod_phase = status.get("phase", "Unknown")

    # Pod conditions
    conditions = {c.get("type"): c.get("status") for c in status.get("conditions", [])}
    pod_initialized = conditions.get("Initialized", "False") == "True"
    pod_scheduled = conditions.get("PodScheduled", "False") == "True"

    # Container statuses
    container_ready = True
    wait_state = None
    terminated = False
    terminated_reason = None
    restart_count = 0

    for cs in status.get("containerStatuses", []):
        restart_count += cs.get("restartCount", 0)
        if not cs.get("ready", False):
            container_ready = False
        # Check waiting state
        waiting = cs.get("state", {}).get("waiting", {})
        if waiting:
            reason = waiting.get("reason", "")
            if reason:
                wait_state = reason
        # Check terminated state
        term = cs.get("state", {}).get("terminated", {})
        if term:
            terminated = True
            terminated_reason = term.get("reason", "")
        # Check last state for terminated info
        last_term = cs.get("lastState", {}).get("terminated", {})
        if last_term and not terminated:
            terminated_reason = last_term.get("reason", "")

    return {
        "pod_phase": pod_phase,
        "container_ready": container_ready,
        "wait_state": wait_state,
        "terminated": terminated,
        "terminated_reason": terminated_reason,
        "restart_count": restart_count,
        "pod_initialized": pod_initialized,
        "pod_scheduled": pod_scheduled,
    }


def get_container_status_signals(container_json):
    """Extract container status signals from Docker inspect JSON.

    Takes the output of `docker inspect <container>` (a single container dict,
    not the list) and extracts status signals matching the fields from
    get_pod_status_signals() for consistency.

    Returns a dict with:
        wait_state: str or None (reason if container is waiting/restarting, e.g. Restarting, Dead)
        terminated: bool (True if container is not running - exited, dead, etc.)
        restart_count: int (number of restarts from container state)
        ready: bool (True if container is running, not restarting, and healthy)
    """
    state = container_json.get("State", {})

    # Extract restart count
    restart_count = state.get("RestartCount", 0)

    # Determine state flags
    running = state.get("Running", False)
    paused = state.get("Paused", False)
    restarting = state.get("Restarting", False)
    dead = state.get("Dead", False)
    oom_killed = state.get("OOMKilled", False)
    exit_code = state.get("ExitCode")

    # Determine wait_state
    wait_state = None
    if restarting:
        wait_state = "Restarting"
    elif dead:
        wait_state = "Dead"
    elif oom_killed:
        wait_state = "OOMKilled"
    elif exit_code is not None and exit_code != 0:
        wait_state = f"Exited ({exit_code})"
    elif exit_code == 0:
        wait_state = "Exited"
    elif not running and not paused and not restarting and not dead and not oom_killed:
        # Container is not running but we don't have specific info - treat as none
        # This handles the case of empty state dict
        wait_state = None

    # terminated is True if container is not in a running state
    terminated = not running or dead or oom_killed or exit_code is not None

    # Determine ready state
    # Container is ready if it's running, not restarting, and healthy
    health = state.get("Health", {})
    health_status = health.get("Status", "") if health else ""
    is_healthy = True
    if health:
        # If health check exists, container is only ready if explicitly "healthy"
        is_healthy = health_status == "healthy"

    ready = running and not restarting and not paused and not dead and is_healthy

    return {
        "wait_state": wait_state,
        "terminated": terminated,
        "restart_count": restart_count,
        "ready": ready,
    }


# ─── Docker Container Discovery ──────────────────────────────────────────

import fnmatch


def _parse_docker_ps_line(line):
    """Parse a single line of docker ps --format json output.
    
    docker ps --format '{{json .}}' outputs one JSON object per line.
    Returns a dict with container info, or None if parsing fails.
    """
    line = line.strip()
    if not line:
        return None
    try:
        return json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return None


def _get_container_inspect(container_id):
    """Get docker inspect output for a container.
    
    Returns the first inspect result (docker inspect returns a list) or None on error.
    """
    cmd = ["docker", "inspect", container_id]
    returncode, stdout, stderr = run_cmd(cmd, timeout=10)
    if returncode != 0 or not stdout:
        return None
    try:
        results = json.loads(stdout)
        return results[0] if results else None
    except (json.JSONDecodeError, ValueError, IndexError):
        return None


def _matches_selectors(container_info, selectors):
    """Check if a container matches the configured selectors.
    
    Args:
        container_info: dict with container info (from docker ps or inspect)
        selectors: dict from get_watch_selectors() with labels, compose_projects, names
        
    Returns:
        bool: True if container matches all configured selectors
    """
    # No selectors means match all
    if not selectors.get("labels") and not selectors.get("compose_projects") and not selectors.get("names"):
        return True
    
    # Check label selectors
    if selectors.get("labels"):
        container_labels = container_info.get("Labels", {})
        # Parse container labels from "key=value,key2=value2" format
        if isinstance(container_labels, str):
            container_labels = parse_watch_labels(container_labels)
        # Check all selector labels are present and match
        for key, value in selectors["labels"].items():
            if container_labels.get(key) != value:
                return False
    
    # Check compose project selectors
    if selectors.get("compose_projects"):
        # Compose project is typically in the label com.docker.compose.project
        container_labels = container_info.get("Labels", {})
        if isinstance(container_labels, str):
            container_labels = parse_watch_labels(container_labels)
        project = container_labels.get("com.docker.compose.project", "")
        if project not in selectors["compose_projects"]:
            return False
    
    # Check name patterns
    if selectors.get("names"):
        container_name = container_info.get("Names", "")
        # Remove leading '/' from container name if present
        if container_name.startswith("/"):
            container_name = container_name[1:]
        # Check if name matches any of the patterns (supports wildcards)
        matched = False
        for pattern in selectors["names"]:
            if fnmatch.fnmatch(container_name, pattern):
                matched = True
                break
        if not matched:
            return False
    
    return True


def discover_docker_containers():
    """Discover Docker containers using docker ps and docker inspect.
    
    Uses docker ps --format '{{json .}}' to list containers, then docker inspect
    to get detailed information. Filters containers based on configured selectors
    from get_watch_selectors().
    
    Returns:
        dict: Dictionary keyed by container ID (short form, e.g. 'abc123') containing:
            - id: str, full container ID
            - name: str, container name (without leading '/')
            - image: str, container image
            - status: str, container status (e.g. 'running', 'exited')
            - state: dict with detailed state info (Running, Paused, Restarting, OOMKilled, etc.)
            - labels: dict, container labels
            - compose_project: str or None, compose project name from labels
            - ports: dict, exposed ports
            - networks: list, network names
            - created_at: str, creation timestamp
            - healthy: bool, health status if health check is configured
            - exit_code: int or None, exit code if container exited
            - restart_count: int, number of restarts
            
        Returns empty dict {} if docker is not available or no containers found.
    """
    selectors = get_watch_selectors()
    
    # If not in Docker mode, return empty
    if selectors.get("runtime") != "docker":
        return {}
    
    # Run docker ps to list all containers
    cmd = ["docker", "ps", "-a", "--format", "{{json .}}"]
    returncode, stdout, stderr = run_cmd(cmd, timeout=10)
    
    if returncode != 0:
        # docker command failed, return empty
        return {}
    
    containers = {}
    for line in stdout.split("\n"):
        container_ps = _parse_docker_ps_line(line)
        if not container_ps:
            continue
        
        container_id = container_ps.get("ID", "")
        if not container_id:
            continue
        
        # Get detailed info from docker inspect
        inspect_info = _get_container_inspect(container_id)
        if not inspect_info:
            # If inspect fails, use ps info only
            inspect_info = {}
        
        # Build container info dict
        container_name = container_ps.get("Names", "") or inspect_info.get("Name", "")
        # Remove leading '/' from name
        if container_name.startswith("/"):
            container_name = container_name[1:]
        
        # Parse labels from ps output (comma-separated key=value)
        labels_str = container_ps.get("Labels", "")
        labels = parse_watch_labels(labels_str) if labels_str else {}
        # Merge with inspect labels if available
        inspect_labels = inspect_info.get("Config", {}).get("Labels", {})
        if inspect_labels:
            labels.update(inspect_labels)
        
        # Get compose project from labels
        compose_project = labels.get("com.docker.compose.project")
        
        # Get state from inspect
        state = inspect_info.get("State", {})
        
        # Determine status
        status = "unknown"
        if state.get("Running"):
            status = "running"
        elif state.get("Paused"):
            status = "paused"
        elif state.get("Restarting"):
            status = "restarting"
        elif state.get("Dead"):
            status = "dead"
        else:
            # Check ExitCode
            if state.get("ExitCode") is not None:
                status = "exited"
        
        # Get ports from inspect
        ports = {}
        network_settings = inspect_info.get("NetworkSettings", {})
        port_bindings = network_settings.get("Ports", {})
        if port_bindings:
            for container_port, bindings in port_bindings.items():
                if bindings:
                    for binding in bindings:
                        host_ip = binding.get("HostIp", "0.0.0.0")
                        host_port = binding.get("HostPort", "")
                        ports[f"{host_ip}:{host_port}"] = container_port
        
        # Get networks
        networks = list(network_settings.get("Networks", {}).keys())
        
        # Get health status
        health = inspect_info.get("State", {}).get("Health", {})
        healthy = None
        if health:
            health_status = health.get("Status", "")
            healthy = health_status == "healthy"
        
        # Build container info
        container_info = {
            "id": container_id,
            "name": container_name,
            "image": container_ps.get("Image", "") or inspect_info.get("Config", {}).get("Image", ""),
            "status": status,
            "state": state,
            "labels": labels,
            "compose_project": compose_project,
            "ports": ports,
            "networks": networks,
            "created_at": container_ps.get("CreatedAt", "") or state.get("StartedAt", ""),
            "healthy": healthy,
            "exit_code": state.get("ExitCode"),
            "restart_count": state.get("RestartCount", 0),
        }
        
        # Check if container matches selectors
        if not _matches_selectors(container_ps, selectors):
            continue
        
        # Use short ID as key for stability
        containers[container_id[:12]] = container_info
    
    return containers
