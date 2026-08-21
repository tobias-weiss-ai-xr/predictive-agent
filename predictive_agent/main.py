#!/usr/bin/env python3
"""openDesk Predictive Agent v4.0 — Reconcile loop.

Collects kubectl metrics, updates Kalman filters and Markov chain state,
runs Bayesian risk prediction, and persists state to PVC.

The reconcile loop is the heart of the operator: every ``RECONCILE_INTERVAL``
seconds it collects metrics from ``kubectl top pods``, ``kubectl top nodes``,
``kubectl get pods -o json``, and ``kubectl logs``, feeds them through the
state model (Kalman trend estimation + Markov chain transitions), generates
predictions via Bayesian risk scoring, and persists everything to the state
model and predictions files on the PVC.

An HTTP server (started in a background thread) exposes /healthz, /ready,
/metrics, /status, /predictions, /state, /history, /cache, and /reanalyze
endpoints.
"""

import json
import logging
import os
import signal
import subprocess
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional

from predictive_agent import config
from predictive_agent.actions import (
    DeploymentScaleAction,
    NodeCordonAction,
    PodRestartAction,
    ResourceTunerAction,
    RightSizeAction,
    RolloutRestartAction,
)
from predictive_agent.collector import (
    collect_top_metrics,
    collect_top_nodes,
    count_log_errors,
    detect_runtime,
    discover_docker_containers,
    get_container_status_signals,
    get_node_conditions,
    get_pod_resources,
    get_pod_status_signals,
    get_watch_selectors,
    parse_memory,
    run_cmd,
)
from predictive_agent.notifier import NotificationManager, create_notifier_from_config
from predictive_agent.persistence import StateStore
from predictive_agent.predictor import Predictor
from predictive_agent.remediator import RemediationManager, create_remediation_manager_from_config
from predictive_agent.state_model import StateModel
from predictive_agent.kg_integration import get_kg_client, DGRAPH_URL
from predictive_agent.backtester import Backtester

logger = logging.getLogger("predictive-agent")

# ─── Global state (shared with server.py) ──────────────────────────────────
_state_model: Optional[StateModel] = None
_predictor: Optional[Predictor] = None
_state_store: Optional[StateStore] = None
_cache: Dict[str, Any] = {}
_history: list = []
_reconcile_count = 0
_last_reconcile_time: Optional[str] = None
_last_reconcile_duration: float = 0.0
_server = None  # HTTP server handle
_remediation_manager: Optional[RemediationManager] = None
_notifier: Optional[NotificationManager] = None
_llm_calls = 0
_llm_errors = 0
_state_saves = 0
_kg_client = None
_backtester = None


def _now_iso() -> str:
    """Return current UTC time as ISO-8601 string."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _kubectl_available() -> bool:
    """Check if kubectl binary is available and can connect to a cluster."""
    rc, _, _ = run_cmd(["kubectl", "version", "--client"], timeout=5)
    return rc == 0


def _get_pods_json() -> dict:
    """Fetch all pods across watched namespaces as JSON."""
    rc, stdout, _ = run_cmd(
        ["kubectl", "get", "pods", "-A", "-o", "json"], timeout=10
    )
    if rc != 0 or not stdout:
        return {"items": []}
    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        return {"items": []}


def _get_pod_restart_count(pod: dict) -> int:
    """Extract total restart count from a pod JSON object."""
    statuses = pod.get("status", {}).get("containerStatuses", [])
    return sum(cs.get("restartCount", 0) for cs in statuses)


def _get_pod_namespace(pod: dict) -> str:
    return pod.get("metadata", {}).get("namespace", "")


def _get_pod_name(pod: dict) -> str:
    return pod.get("metadata", {}).get("name", "")


def _get_pod_memory_limit(pod: dict, container_name: str = "") -> int:
    """Extract memory limit in MiB from pod spec."""
    spec = pod.get("spec", {})
    for container in spec.get("containers", []):
        if container_name and container.get("name", "") != container_name:
            continue
        limits = container.get("resources", {}).get("limits", {})
        if "memory" in limits:
            from predictive_agent.collector import parse_memory
            return parse_memory(limits["memory"])
    return 0


def _should_skip_namespace(ns: str) -> bool:
    """Check if a namespace should be skipped."""
    if ns in config.SKIP_NAMESPACES:
        return True
    if config.WATCH_NAMESPACES and ns not in config.WATCH_NAMESPACES:
        return True
    return False


def _get_node_pressure(node_conditions: dict, node_name: str) -> tuple[bool, bool]:
    """Check if a node has memory or disk pressure."""
    conds = node_conditions.get(node_name, {})
    mem_pressure = conds.get("MemoryPressure", "False") == "True"
    disk_pressure = conds.get("DiskPressure", "False") == "True"
    return mem_pressure, disk_pressure


def _matches_docker_selectors(container_info: dict, selectors: dict) -> bool:
    """Check if a Docker container matches configured selectors.
    
    Args:
        container_info: dict with container info from discover_docker_containers
        selectors: dict from get_watch_selectors() with labels, compose_projects, names
        
    Returns:
        bool: True if container matches all configured selectors
    """
    import fnmatch
    
    # No selectors means match all
    if not selectors.get("labels") and not selectors.get("compose_projects") and not selectors.get("names"):
        return True
    
    # Check label selectors
    if selectors.get("labels"):
        container_labels = container_info.get("labels", {})
        for key, value in selectors["labels"].items():
            if container_labels.get(key) != value:
                return False
    
    # Check compose project selectors
    if selectors.get("compose_projects"):
        project = container_info.get("compose_project", "")
        if project not in selectors["compose_projects"]:
            return False
    
    # Check name patterns
    if selectors.get("names"):
        container_name = container_info.get("name", "")
        matched = False
        for pattern in selectors["names"]:
            if fnmatch.fnmatch(container_name, pattern):
                matched = True
                break
        if not matched:
            return False
    
    return True


def _collect_docker_container_metrics() -> dict:
    """Collect Docker container metrics using docker stats.
    
    Returns:
        dict: Container ID -> {cpu_m, memory_mib} mapping
    """
    metrics = {}
    try:
        # Use docker stats --no-stream --format to get current metrics
        rc, stdout, _ = run_cmd(
            ["docker", "stats", "--no-stream", "--format", "{{.Container}},{{.CPUPerc}},{{.MemUsage}}"],
            timeout=10
        )
        if rc == 0 and stdout:
            for line in stdout.strip().split("\n"):
                if not line.strip():
                    continue
                parts = line.split(",")
                if len(parts) >= 3:
                    container_id = parts[0].strip()
                    cpu_pct_str = parts[1].strip().rstrip("%")
                    mem_usage_str = parts[2].strip()
                    
                    try:
                        cpu_pct = float(cpu_pct_str)
                        cpu_m = int(cpu_pct * 10)  # Convert % to millicores (assuming 100% = 1000m)
                        
                        # Parse memory usage like "50MiB / 1GiB"
                        mem_usage = mem_usage_str.split("/")[0].strip()
                        memory_mib = parse_memory(mem_usage)
                        
                        # Use short container ID as key
                        short_id = container_id[:12] if len(container_id) > 12 else container_id
                        metrics[short_id] = {
                            "cpu_m": cpu_m,
                            "memory_mib": memory_mib,
                        }
                    except (ValueError, IndexError):
                        continue
    except Exception as e:
        logger.debug("Failed to collect Docker metrics: %s", e)
    
    return metrics


def _get_container_memory_limit(inspect_info: dict) -> int:
    """Extract memory limit from Docker inspect info in MiB."""
    try:
        # Check HostConfig.Memory (in bytes) first
        memory_bytes = inspect_info.get("HostConfig", {}).get("Memory", 0)
        if memory_bytes > 0:
            return memory_bytes // (1024 * 1024)  # Convert to MiB
        
        # Fallback: check Config.Memory if it exists
        memory_bytes = inspect_info.get("Config", {}).get("Memory", 0)
        if memory_bytes > 0:
            return memory_bytes // (1024 * 1024)
        
        return 0
    except (TypeError, ValueError):
        return 0


def reconcile() -> Dict[str, Any]:
    """Run one reconcile cycle.

    Collects metrics, updates state model, generates predictions, persists
    state, and returns a summary dict.

    Handles both Docker and Kubernetes runtimes based on configuration.

    Returns:
        Dict with keys: predictions, state, timestamp, pods_tracked,
        at_risk_count, reconcile_count.
    """
    global _reconcile_count, _last_reconcile_time, _last_reconcile_duration

    _reconcile_count += 1
    cycle = _reconcile_count
    logger.info("Reconcile #%d: starting", cycle)

    import time as _time_mod
    _reconcile_start = _time_mod.monotonic()

    # ─── Detect runtime and collect metrics ────────────────────────────
    runtime = detect_runtime()
    logger.debug("Reconcile #%d: runtime=%s", cycle, runtime)
    
    # Track currently seen containers/pods for cleanup
    current_keys = set()
    pod_metrics = {}
    node_conditions = {}
    
    if runtime == "docker":
        # Docker mode: discover containers and their metrics
        containers = discover_docker_containers()
        docker_metrics = _collect_docker_container_metrics()
        
        pod_metrics = docker_metrics
        # For Docker, node_conditions is not applicable
        node_conditions = {}
        
        # Build list of container items with pod-like structure
        pod_items = []
        for container_id, info in containers.items():
            # Create a pod-like dict for processing
            # Use compose_project as namespace, or "docker" as default
            ns = info.get("compose_project", "docker") or "docker"
            name = info.get("name", container_id[:12])
            pod_key = f"{ns}/{name}"
            
            pod_items.append({
                "metadata": {
                    "namespace": ns,
                    "name": name,
                    "creationTimestamp": info.get("created_at", ""),
                },
                "spec": {
                    "nodeName": "docker-host",
                },
                "status": {
                    "phase": "Running" if info.get("status") == "running" else "Succeeded" if info.get("status") == "exited" else "Unknown",
                    "containerStatuses": [{
                        "ready": info.get("healthy", True),
                        "restartCount": info.get("restart_count", 0),
                    }],
                },
                "_container_id": container_id,
                "_docker_info": info,
            })
            current_keys.add(pod_key)
    else:
        # Kubernetes mode: use kubectl commands
        rc_pods, pods_output, _ = run_cmd(
            ["kubectl", "top", "pods", "-A", "--no-headers"], timeout=10
        )
        pod_metrics = collect_top_metrics(pods_output) if rc_pods == 0 else {}

        rc_nodes, nodes_output, _ = run_cmd(
            ["kubectl", "top", "nodes", "--no-headers"], timeout=10
        )
        node_metrics = collect_top_nodes(nodes_output) if rc_nodes == 0 else {}

        pods_json = _get_pods_json()
        
        rc_nodes_json, nodes_json_output, _ = run_cmd(
            ["kubectl", "get", "nodes", "-o", "json"], timeout=10
        )
        if rc_nodes_json == 0 and nodes_json_output:
            try:
                node_conditions = get_node_conditions(json.loads(nodes_json_output))
            except json.JSONDecodeError:
                pass
        
        pod_items = pods_json.get("items", [])
        
        # Track current pod keys for cleanup
        for pod in pod_items:
            ns = _get_pod_namespace(pod)
            name = _get_pod_name(pod)
            if not _should_skip_namespace(ns):
                current_keys.add(f"{ns}/{name}")

    # ─── Update state model ────────────────────────────────────────────
    if _state_model is None:
        logger.warning("State model not initialized")
        return {
            "predictions": [],
            "state": {},
            "timestamp": _now_iso(),
            "pods_tracked": 0,
            "at_risk_count": 0,
            "reconcile_count": cycle,
        }

    # Finalize removed containers/pods
    removed_keys = set(_state_model.pods.keys()) - current_keys
    for pod_key in removed_keys:
        # Parse pod_key to get namespace and name
        parts = pod_key.split("/", 1)
        if len(parts) == 2:
            ns, name = parts
            tracker = _state_model.pods.get(pod_key)
            if tracker and tracker.state != "FAILED":
                # Record final transition to FAILED
                _state_model.markov.record_transition(tracker.state, "FAILED")
            logger.info("Finalizing removed container/pod: %s", pod_key)
            # Remove from state model
            if pod_key in _state_model.pods:
                del _state_model.pods[pod_key]

    pods_tracked = 0
    at_risk_count = 0

    for pod in pod_items:
        # Extract namespace and name (works for both K8s and Docker)
        if runtime == "docker":
            # Docker: use _docker_info
            ns = pod.get("metadata", {}).get("namespace", "docker")
            name = pod.get("metadata", {}).get("name", "")
            container_id = pod.get("_container_id", "")
            docker_info = pod.get("_docker_info", {})
            
            # For Docker, skip namespace filtering
            # but respect watch selectors if configured
            selectors = get_watch_selectors()
            if selectors.get("runtime") == "docker":
                # Check if container matches selectors
                if not _matches_docker_selectors(docker_info, selectors):
                    continue
            
            pod_key = f"{ns}/{name}"
            
            # Get restart count from Docker info
            restart_count = docker_info.get("restart_count", 0)
            
            # Get metrics from docker stats (keyed by container id)
            metrics = pod_metrics.get(container_id[:12], {})
            cpu_m = metrics.get("cpu_m", 0)
            memory_mib = metrics.get("memory_mib", 0)
            
            # Get memory limit from Docker inspect info
            memory_limit_mib = _get_container_memory_limit(pod.get("_docker_info", {}))
            
            # Docker has no node pressure (single host)
            mem_pressure = False
            
            # Get pod status signals from Docker info
            status_signals = get_container_status_signals(pod.get("_docker_info", {}))
            pod_phase = status_signals.get("pod_phase", "Running")
            container_ready = status_signals.get("container_ready", True)
            wait_state = status_signals.get("wait_state")
            terminated = status_signals.get("terminated", False)
            terminated_reason = status_signals.get("terminated_reason")
            pod_scheduled = True  # Docker containers are always scheduled
            
            # Collect log errors from Docker logs
            log_errors = 0
            if pod_phase not in config.SKIP_LOGS_STATUSES and name:
                # Try to get logs from the container
                rc_logs, logs_output, _ = run_cmd(
                    ["docker", "logs", name, "--tail=50"],
                    timeout=5,
                )
                if rc_logs == 0:
                    log_errors = count_log_errors(logs_output)
        else:
            # Kubernetes mode
            ns = _get_pod_namespace(pod)
            name = _get_pod_name(pod)

            if _should_skip_namespace(ns):
                continue

            pod_key = f"{ns}/{name}"
            restart_count = _get_pod_restart_count(pod)

            # Get metrics from kubectl top
            metrics = pod_metrics.get(pod_key, {})
            cpu_m = metrics.get("cpu_m", 0)
            memory_mib = metrics.get("memory_mib", 0)

            # Get memory limit from pod spec
            memory_limit_mib = _get_pod_memory_limit(pod)

            # Get node pressure
            node_name = pod.get("spec", {}).get("nodeName", "")
            mem_pressure, _disk_pressure = _get_node_pressure(node_conditions, node_name)

            # Get pod status signals (phase, container ready, wait state, etc.)
            status_signals = get_pod_status_signals(pod)
            pod_phase = status_signals["pod_phase"]
            container_ready = status_signals["container_ready"]
            wait_state = status_signals["wait_state"]
            terminated = status_signals["terminated"]
            terminated_reason = status_signals["terminated_reason"]
            pod_scheduled = status_signals["pod_scheduled"]

            # Collect log errors (skip for certain statuses to save API calls)
            pod_phase = pod.get("status", {}).get("phase", "")
            log_errors = 0
            if pod_phase not in config.SKIP_LOGS_STATUSES:
                container_statuses = pod.get("status", {}).get("containerStatuses", [])
                for cs in container_statuses:
                    if cs.get("ready", False) is False or cs.get("restartCount", 0) > 0:
                        rc_logs, logs_output, _ = run_cmd(
                            ["kubectl", "logs", "-n", ns, name, "--tail=50"],
                            timeout=5,
                        )
                        if rc_logs == 0:
                            log_errors = count_log_errors(logs_output)
                        break

        # Update state model
        tracker = _state_model.update_pod(
            namespace=ns,
            name=name,
            memory_mib=memory_mib,
            memory_limit_mib=memory_limit_mib,
            cpu_m=cpu_m,
            restart_count=restart_count,
            log_errors=log_errors,
            node_pressure=mem_pressure,
        )
        pods_tracked += 1

        # Generate prediction
        if _predictor is not None:
            markov_state = tracker.state
            markov_p_critical = 0.0
            markov_p_failed = 0.0
            if _state_model.markov:
                transitions = _state_model.markov.predict(markov_state, steps=1)
                markov_p_critical = transitions.get("CRITICAL", 0.0)
                markov_p_failed = transitions.get("FAILED", 0.0)

            # Calculate restart rate per hour from pod age (not raw count)
            ct = pod.get("metadata", {}).get("creationTimestamp", "")
            if ct:
                try:
                    pod_age_s = (datetime.now(timezone.utc) - datetime.fromisoformat(
                        ct.replace("Z", "+00:00")
                    )).total_seconds()
                except ValueError:
                    pod_age_s = 3600
            else:
                pod_age_s = 3600
            restart_rate = restart_count / max(pod_age_s / 3600, 1.0)  # restarts per hour
            log_error_rate = log_errors / 60.0  # Approximate per-minute rate

            # Query knowledge graph for blast radius (0 if KG unavailable)
            blast_radius = 0
            if _kg_client is not None:
                try:
                    blast_radius = _kg_client.get_blast_radius(pod_key)
                except Exception:
                    blast_radius = 0

            result = _predictor.predict(
                pod_key=pod_key,
                memory_pct=tracker.memory_pct,
                memory_trend_mib_per_min=tracker.memory_trend,
                memory_limit_mib=memory_limit_mib,
                memory_mib=memory_mib,
                cpu_pct=tracker.cpu_pct,
                restart_rate_per_hr=restart_rate,
                log_error_rate_per_min=log_error_rate,
                node_memory_pressure=mem_pressure,
                node_disk_pressure=False,
                markov_state=markov_state,
                markov_p_critical=markov_p_critical,
                markov_p_failed=markov_p_failed,
                cpu_trend_m_per_min=tracker.cpu_trend,
                memory_anomaly_score=tracker.memory_anomaly_score,
                cpu_anomaly_score=tracker.cpu_anomaly_score,
                blast_radius=blast_radius,
                pod_phase=pod_phase,
                container_ready=container_ready,
                wait_state=wait_state,
                terminated=terminated,
                terminated_reason=terminated_reason,
                pod_scheduled=pod_scheduled,
            )

            if result.risk_score >= _predictor.risk_threshold:
                at_risk_count += 1
                logger.warning(
                    "Pod %s at risk: score=%.2f ttf=%s state=%s",
                    pod_key, result.risk_score, result.ttf_minutes, markov_state,
                )

                # ─── Remediation (REM-8) ────────────────────────────────
                if _remediation_manager is not None:
                    # pod_state already has cpu_trend/memory_trend via Kalman filters
                    try:
                        results = _remediation_manager.evaluate(
                            tracker, result, result.risk_score
                        )
                        for rem_result in results:
                            logger.info(
                                "Remediation: %s on %s — %s",
                                rem_result.action,
                                rem_result.target,
                                rem_result.message,
                            )
                            # Send notification for each remediation action
                            if _notifier is not None and rem_result.success:
                                _notifier.notify(
                                    alert_type="remediation",
                                    pod_name=pod_key,
                                    risk_score=result.risk_score,
                                    action_taken=rem_result.action,
                                    details=rem_result.message,
                                )
                    except Exception as e:
                        logger.error("Remediation evaluation failed for %s: %s", pod_key, e)

    # Update Markov chain
    if _state_model.markov:
        for pod_key, tracker in _state_model.pods.items():
            _state_model.markov.record_transition(tracker.prev_state, tracker.state)

    # ─── Persist state ─────────────────────────────────────────────────
    if _state_store is not None:
        try:
            _state_store.save_markov(_state_model.markov)
            global _state_saves
            _state_saves += 1
            if _predictor is not None:
                predictions_data = [
                    {
                        "pod_key": p.pod_key,
                        "risk_score": p.risk_score,
                        "ttf_minutes": p.ttf_minutes,
                        "confidence": p.confidence,
                        "markov_state": p.markov_state,
                        "memory_trend": p.memory_trend,
                        "cpu_trend": p.cpu_trend,
                        "memory_pct": p.memory_pct,
                        "cpu_pct": p.cpu_pct,
                    }
                    for p in _predictor._predictions.values()
                ]
                _state_store.save_predictions(predictions_data)
        except Exception as e:
            logger.error("Failed to persist state: %s", e)

    # ─── Record predictions for backtesting ───────────────────────────
    if _backtester is not None and _predictor is not None:
        for pod_key, tracker in _state_model.pods.items():
            pred = _predictor._predictions.get(pod_key)
            if pred is not None:
                try:
                    _backtester.record_prediction(
                        pod_key=pod_key,
                        risk_score=pred.risk_score,
                        ttf_minutes=pred.ttf_minutes,
                        confidence=pred.confidence,
                        markov_state=pred.markov_state,
                        memory_pct=pred.memory_pct,
                        cpu_pct=pred.cpu_pct,
                        memory_trend=pred.memory_trend,
                        cpu_trend=pred.cpu_trend,
                    )
                    # Record outcome: failure if pod is in unhealthy state or has restarts
                    actual_failure = (
                        tracker.state in ("CRITICAL", "FAILED")
                        or tracker.restart_count > 0
                    )
                    _backtester.record_outcome(
                        pod_key=pod_key,
                        actual_failure=actual_failure,
                        restart_count=tracker.restart_count,
                        state=tracker.state,
                    )
                except Exception as e:
                    logger.debug("Backtester recording failed for %s: %s", pod_key, e)

    _last_reconcile_time = _now_iso()

    result = {
        "predictions": [
            {
                "pod_key": p.pod_key,
                "risk_score": p.risk_score,
                "ttf_minutes": p.ttf_minutes,
            }
            for p in (_predictor._predictions.values() if _predictor else [])
        ],
        "state": _state_model.to_dict() if _state_model else {},
        "timestamp": _last_reconcile_time,
        "pods_tracked": pods_tracked,
        "at_risk_count": at_risk_count,
        "reconcile_count": cycle,
    }

    # ─── Add remediation stats to result ──────────────────────────────
    if _remediation_manager is not None:
        result["remediation"] = _remediation_manager.get_stats()

    if at_risk_count > 0:
        logger.warning("Reconcile #%d: %d pods at risk", cycle, at_risk_count)
    else:
        logger.info("Reconcile #%d: %d pods tracked, 0 at risk", cycle, pods_tracked)

    # ─── Update server context for Prometheus metrics ─────────────────
    _last_reconcile_duration = _time_mod.monotonic() - _reconcile_start
    try:
        from predictive_agent.server import _context as _server_context
        _server_context.reconcile_count = cycle
        _server_context.reconcile_duration = round(_last_reconcile_duration, 3)
        _server_context.at_risk_count = at_risk_count
        _server_context.llm_calls = _llm_calls
        _server_context.llm_errors = _llm_errors
        _server_context.state_saves = _state_saves
    except Exception:
        pass

    return result


class ReconcileLoop:
    """Background reconcile loop running in a daemon thread."""

    def __init__(self, interval: int = 60):
        self.interval = interval
        self.running = False
        self._thread: Optional[threading.Thread] = None
        self._reconcile_fn: Callable = reconcile

    def start(self) -> None:
        """Start the reconcile loop in a background daemon thread."""
        if self.running:
            return
        self.running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop the reconcile loop and wait for the thread to finish."""
        self.running = False
        if self._thread:
            self._thread.join(timeout=5)

    def _run(self) -> None:
        """Main loop body — calls reconcile at each interval."""
        while self.running:
            start = time.monotonic()
            try:
                self._reconcile_fn()
            except Exception as e:
                logger.error("Reconcile error: %s", e)
            elapsed = time.monotonic() - start
            sleep_time = max(0, self.interval - elapsed)
            # Sleep in small increments so stop() is responsive
            slept = 0.0
            while slept < sleep_time and self.running:
                time.sleep(min(0.5, sleep_time - slept))
                slept += 0.5


def _setup_logging() -> None:
    """Configure logging based on LOG_VERBOSITY env var."""
    level = logging.INFO
    if config.LOG_VERBOSITY.lower() == "debug":
        level = logging.DEBUG
    elif config.LOG_VERBOSITY.lower() == "warn":
        level = logging.WARNING
    logging.basicConfig(
        level=level,
        format="[%(asctime)s] [%(levelname)s] %(message)s",
    )


def _start_http_server() -> Any:
    """Start the HTTP metrics/health server."""
    from predictive_agent.server import start_server

    return start_server(
        metrics_port=config.METRICS_PORT,
        health_port=config.HEALTH_PORT,
        state_model=_state_model,
        predictor=_predictor,
        cache=_cache,
        reconcile_callback=reconcile,
        history=_history,
        remediation_manager=_remediation_manager,
        notifier=_notifier,
        backtester=_backtester,
    )


def main() -> None:
    """Main entry point — initialize state, start server, run reconcile loop."""
    global _state_model, _predictor, _state_store, _server
    global _remediation_manager, _notifier, _kg_client, _backtester

    _setup_logging()
    logger.info("=== %s v%s starting ===", config.OPERATOR_NAME, config.OPERATOR_VERSION)
    
    # Detect runtime and get watch selectors
    runtime = detect_runtime()
    selectors = get_watch_selectors()
    logger.info("Runtime: %s", runtime)
    
    if runtime == "docker":
        logger.info("Docker watch labels: %s", selectors.get("labels", {}))
        logger.info("Docker watch compose projects: %s", selectors.get("compose_projects", []))
        logger.info("Docker watch names: %s", selectors.get("names", []))
    else:
        logger.info("Watch namespaces: %s", selectors.get("namespaces", []))
    
    logger.info("Reconcile interval: %ds", config.RECONCILE_INTERVAL)
    logger.info("LLM backend: %s", config.LLM_BACKEND)

    # Initialize state
    _state_model = StateModel()
    _predictor = Predictor(risk_threshold=config.PREDICTION_RISK_THRESHOLD)
    _state_store = StateStore(
        state_model_file=config.STATE_MODEL_FILE,
        predictions_file=config.PREDICTIONS_FILE,
    )

    # Load persisted state
    try:
        _state_model.markov = _state_store.load_markov()
        logger.info("Loaded Markov chain state from %s", config.STATE_MODEL_FILE)
    except Exception as e:
        logger.warning("Could not load Markov state: %s", e)

    # ─── Initialize remediation (REM-8) ───────────────────────────────
    _remediation_manager = create_remediation_manager_from_config()
    _remediation_manager.register_action(PodRestartAction())
    _remediation_manager.register_action(NodeCordonAction())
    _remediation_manager.register_action(RightSizeAction())
    _remediation_manager.register_action(RolloutRestartAction())
    _remediation_manager.register_action(DeploymentScaleAction())
    _remediation_manager.register_action(ResourceTunerAction())
    logger.info(
        "Remediation initialized: dry_run=%s, threshold=%.1f, actions=%s",
        _remediation_manager.dry_run,
        _remediation_manager.risk_threshold,
        _remediation_manager.get_stats()["registered_actions"],
    )

    # ─── Initialize notifications (REM-6) ──────────────────────────────
    _notifier = create_notifier_from_config()
    logger.info("Notification manager initialized")

    # ─── Initialize knowledge graph integration ────────────────────────
    _kg_client = get_kg_client()
    if _kg_client and _kg_client.health():
        logger.info("Knowledge graph connected at %s", DGRAPH_URL)
    else:
        logger.info("Knowledge graph not available (blast radius will be 0)")
        _kg_client = None

    # ─── Initialize backtester ────────────────────────────────────────
    _backtester = Backtester()
    logger.info("Backtester initialized (history: %d predictions, %d outcomes)",
                _backtester.get_prediction_count(), _backtester.get_outcome_count())

    # Start HTTP server
    try:
        _server = _start_http_server()
        logger.info("HTTP server started on %d (metrics) and %d (health)",
                    config.METRICS_PORT, config.HEALTH_PORT)
    except Exception as e:
        logger.error("Failed to start HTTP server: %s", e)

    # Handle signals for graceful shutdown
    loop = ReconcileLoop(interval=config.RECONCILE_INTERVAL)

    def _shutdown(signum, frame):
        logger.info("Received signal %d, shutting down...", signum)
        loop.stop()
        if _server:
            _server.shutdown()
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    # Start reconcile loop
    loop.start()
    logger.info("Reconcile loop started with interval %ds", config.RECONCILE_INTERVAL)

    # Keep main thread alive
    try:
        while True:
            time.sleep(3600)
    except (KeyboardInterrupt, SystemExit):
        loop.stop()
        if _server:
            _server.shutdown()


if __name__ == "__main__":
    main()
