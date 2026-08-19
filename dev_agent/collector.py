"""Metrics collection from kubectl (top pods, get pods, logs, node conditions)."""

import json
import re
import subprocess


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
    error_patterns = [
        r"\bERROR\b",
        r"\bError\b",
        r"\bFATAL\b",
        r"\bPANIC\b",
        r"\bOOM\b",
        r"\bCrashLoopBackOff\b",
        r"\bException\b",
        r"\bTraceback\b",
        r"\bpanic:",
        r"\bfatal:",
    ]
    count = 0
    for line in log_text.split("\n"):
        for pattern in error_patterns:
            if re.search(pattern, line):
                count += 1
                break
    return count
