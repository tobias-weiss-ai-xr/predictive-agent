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
from predictive_agent.collector import (
    collect_top_metrics,
    collect_top_nodes,
    count_log_errors,
    get_node_conditions,
    get_pod_resources,
    run_cmd,
)
from predictive_agent.persistence import StateStore
from predictive_agent.predictor import Predictor
from predictive_agent.state_model import StateModel

logger = logging.getLogger("predictive-agent")

# ─── Global state (shared with server.py) ──────────────────────────────────
_state_model: Optional[StateModel] = None
_predictor: Optional[Predictor] = None
_state_store: Optional[StateStore] = None
_cache: Dict[str, Any] = {}
_history: list = []
_reconcile_count = 0
_last_reconcile_time: Optional[str] = None
_server = None  # HTTP server handle


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


def reconcile() -> Dict[str, Any]:
    """Run one reconcile cycle.

    Collects metrics, updates state model, generates predictions, persists
    state, and returns a summary dict.

    Returns:
        Dict with keys: predictions, state, timestamp, pods_tracked,
        at_risk_count, reconcile_count.
    """
    global _reconcile_count, _last_reconcile_time

    _reconcile_count += 1
    cycle = _reconcile_count
    logger.info("Reconcile #%d: starting", cycle)

    # ─── Collect metrics ───────────────────────────────────────────────
    # Use short timeouts so a missing cluster doesn't block the loop.
    rc_pods, pods_output, _ = run_cmd(
        ["kubectl", "top", "pods", "-A", "--no-headers"], timeout=10
    )
    pod_metrics = collect_top_metrics(pods_output) if rc_pods == 0 else {}

    rc_nodes, nodes_output, _ = run_cmd(
        ["kubectl", "top", "nodes", "--no-headers"], timeout=10
    )
    node_metrics = collect_top_nodes(nodes_output) if rc_nodes == 0 else {}

    pods_json = _get_pods_json()
    pod_items = pods_json.get("items", [])

    rc_nodes_json, nodes_json_output, _ = run_cmd(
        ["kubectl", "get", "nodes", "-o", "json"], timeout=10
    )
    node_conditions = {}
    if rc_nodes_json == 0 and nodes_json_output:
        try:
            node_conditions = get_node_conditions(json.loads(nodes_json_output))
        except json.JSONDecodeError:
            pass

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

    pods_tracked = 0
    at_risk_count = 0

    for pod in pod_items:
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

            restart_rate = restart_count  # Simplified: restarts per cycle
            log_error_rate = log_errors / 60.0  # Approximate per-minute rate

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
            )

            if result.risk_score >= _predictor.risk_threshold:
                at_risk_count += 1
                logger.warning(
                    "Pod %s at risk: score=%.2f ttf=%s state=%s",
                    pod_key, result.risk_score, result.ttf_minutes, markov_state,
                )

    # Update Markov chain
    if _state_model.markov:
        for pod_key, tracker in _state_model.pods.items():
            _state_model.markov.record_transition(tracker.prev_state, tracker.state)

    # ─── Persist state ─────────────────────────────────────────────────
    if _state_store is not None:
        try:
            _state_store.save_markov(_state_model.markov)
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

    if at_risk_count > 0:
        logger.warning("Reconcile #%d: %d pods at risk", cycle, at_risk_count)
    else:
        logger.info("Reconcile #%d: %d pods tracked, 0 at risk", cycle, pods_tracked)

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
    )


def main() -> None:
    """Main entry point — initialize state, start server, run reconcile loop."""
    global _state_model, _predictor, _state_store, _server

    _setup_logging()
    logger.info("=== %s v%s starting ===", config.OPERATOR_NAME, config.OPERATOR_VERSION)
    logger.info("Watch namespaces: %s", config.WATCH_NAMESPACES)
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
