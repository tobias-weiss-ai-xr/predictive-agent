"""HTTP server exposing health, metrics, predictions, and state endpoints.

The server is started with :func:`start_server`, which accepts an optional
:class:`~predictive_agent.state_model.StateModel` and
:class:`~predictive_agent.predictor.Predictor`. When provided, the API
endpoints serve *real* data (live pod states, real predictions, real Prometheus
gauges). When omitted (e.g. in the test suite), the endpoints return sensible
empty defaults so the server always responds correctly.
"""

import json
import threading
import time
from dataclasses import asdict, is_dataclass
from http.server import HTTPServer as BaseHTTPServer, BaseHTTPRequestHandler

from predictive_agent import config


# ─── Shared server context ───────────────────────────────────────────────────
class _ServerState:
    """Mutable container shared between request handlers and the operator.

    Populated by :func:`start_server` so that handlers can serve real data
    without relying on hard module-level globals that are awkward to reset
    between test runs / server restarts.
    """

    __slots__ = (
        "state_model",
        "predictor",
        "cache",
        "reconcile_callback",
        "history",
        "start_time",
        "remediation_manager",
        "notifier",
        # Reconcile stats (updated by main.py reconcile loop)
        "reconcile_count",
        "reconcile_duration",
        "at_risk_count",
        "llm_calls",
        "llm_errors",
        "state_saves",
    )

    def __init__(self):
        self.state_model = None
        self.predictor = None
        self.cache = None
        self.reconcile_callback = None
        self.history = []
        self.start_time = time.time()
        self.remediation_manager = None
        self.notifier = None
        self.reconcile_count = 0
        self.reconcile_duration = 0.0
        self.at_risk_count = 0
        self.llm_calls = 0
        self.llm_errors = 0
        self.state_saves = 0


# Default context used when start_server is called without arguments (e.g. the
# test suite). start_server replaces this with a fresh, populated instance.
_context = _ServerState()


# ─── Helpers ─────────────────────────────────────────────────────────────────
def _all_predictions(predictor):
    """Return all stored PredictionResult objects from a Predictor."""
    if predictor is None:
        return []
    preds = getattr(predictor, "_predictions", None)
    if not preds:
        # Fall back to the public at-risk API if the internal store is empty
        getter = getattr(predictor, "get_at_risk", None)
        if callable(getter):
            try:
                return list(getter())
            except Exception:
                return []
        return []
    return list(preds.values())


def _serialize_prediction(result):
    """Convert a PredictionResult into a JSON-serializable dict.

    Exposes ``ttf`` as an alias for ``ttf_minutes`` per the HTTP API contract.
    """
    if is_dataclass(result):
        data = asdict(result)
    else:
        data = dict(getattr(result, "__dict__", {}))
    if "ttf_minutes" in data:
        data.setdefault("ttf", data["ttf_minutes"])
    return data


# ─── Request handler ─────────────────────────────────────────────────────────
class RequestHandler(BaseHTTPRequestHandler):
    """HTTP request handler serving all metrics/API and health endpoints."""

    def _send_response(self, data, status=200, content_type="application/json"):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.end_headers()
        if isinstance(data, (dict, list)):
            self.wfile.write(json.dumps(data).encode())
        else:
            self.wfile.write(data.encode())

    # ── Routing ──────────────────────────────────────────────────────────────
    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/healthz":
            self._send_response({"status": "ok"})
        elif path == "/ready":
            self._send_response({"status": "ready"})
        elif path == "/metrics":
            self._send_response(self._metrics_text(),
                                content_type="text/plain; version=0.0.4; charset=utf-8")
        elif path == "/status":
            self._send_response(self._status_dict())
        elif path == "/predictions":
            self._send_response(self._predictions_dict())
        elif path == "/state":
            self._send_response(self._state_dict())
        elif path == "/history":
            self._send_response(_context.history if _context.history else [])
        elif path == "/reanalyze":
            data, status = self._reanalyze()
            self._send_response(data, status=status)
        elif path == "/cache":
            self._send_response(self._cache_dict())
        elif path == "/remediate":
            self._send_response(self._remediate_get())
        elif path == "/notifications":
            self._send_response(self._notifications_get())
        else:
            self._send_response({"error": "Not Found"}, status=404)

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if path == "/reanalyze":
            data, status = self._reanalyze()
            self._send_response(data, status=status)
        elif path == "/remediate":
            data, status = self._remediate_post()
            self._send_response(data, status=status)
        else:
            self._send_response({"error": "Not Found"}, status=404)

    # ── Endpoint payloads ────────────────────────────────────────────────────
    # State name to numeric mapping for Prometheus
    _STATE_MAP = {
        "HEALTHY": 0, "STABLE": 1, "DEGRADED": 2,
        "WARNING": 3, "CRITICAL": 4, "FAILED": 5,
    }

    def _metrics_text(self):
        """Prometheus-format metrics with real gauges from state/predictor."""
        sm = _context.state_model
        pred = _context.predictor

        pods_tracked = len(sm.pods) if sm is not None else 0
        predictions = _all_predictions(pred)
        predictions_count = len(predictions)
        risk_score = max((p.risk_score for p in predictions), default=0.0)
        uptime = int(time.time() - _context.start_time)
        at_risk = _context.at_risk_count
        reconcile_count = _context.reconcile_count
        reconcile_duration = _context.reconcile_duration
        llm_calls = _context.llm_calls
        llm_errors = _context.llm_errors
        state_saves = _context.state_saves

        lines = [
            "# HELP opendesk_predictive_agent_pods_tracked Number of pods currently tracked",
            "# TYPE opendesk_predictive_agent_pods_tracked gauge",
            f"opendesk_predictive_agent_pods_tracked {pods_tracked}",
            "# HELP opendesk_predictive_agent_pods_at_risk Number of pods at risk (risk >= threshold)",
            "# TYPE opendesk_predictive_agent_pods_at_risk gauge",
            f"opendesk_predictive_agent_pods_at_risk {at_risk}",
            "# HELP opendesk_predictive_agent_predictions_count Number of active predictions",
            "# TYPE opendesk_predictive_agent_predictions_count gauge",
            f"opendesk_predictive_agent_predictions_count {predictions_count}",
            "# HELP opendesk_predictive_agent_risk_score Highest current risk score (0-1)",
            "# TYPE opendesk_predictive_agent_risk_score gauge",
            f"opendesk_predictive_agent_risk_score {risk_score}",
            "# HELP opendesk_predictive_agent_uptime_seconds Uptime in seconds",
            "# TYPE opendesk_predictive_agent_uptime_seconds gauge",
            f"opendesk_predictive_agent_uptime_seconds {uptime}",
            "# HELP opendesk_predictive_agent_reconcile_total Total reconcile cycles",
            "# TYPE opendesk_predictive_agent_reconcile_total counter",
            f"opendesk_predictive_agent_reconcile_total {reconcile_count}",
            "# HELP opendesk_predictive_agent_reconcile_duration_seconds Duration of last reconcile in seconds",
            "# TYPE opendesk_predictive_agent_reconcile_duration_seconds gauge",
            f"opendesk_predictive_agent_reconcile_duration_seconds {reconcile_duration}",
            "# HELP opendesk_predictive_agent_llm_calls_total Total LLM analysis calls",
            "# TYPE opendesk_predictive_agent_llm_calls_total counter",
            f"opendesk_predictive_agent_llm_calls_total {llm_calls}",
            "# HELP opendesk_predictive_agent_llm_errors_total Total LLM analysis errors",
            "# TYPE opendesk_predictive_agent_llm_errors_total counter",
            f"opendesk_predictive_agent_llm_errors_total {llm_errors}",
            "# HELP opendesk_predictive_agent_state_saves_total Total state model saves to disk",
            "# TYPE opendesk_predictive_agent_state_saves_total counter",
            f"opendesk_predictive_agent_state_saves_total {state_saves}",
            "# HELP opendesk_predictive_agent_pod_risk_score Per-pod risk score (0-1)",
            "# TYPE opendesk_predictive_agent_pod_risk_score gauge",
        ]
        # Per-pod risk score metrics
        for p in predictions:
            pod_key = p.pod_key if hasattr(p, 'pod_key') else p.get('pod_key', 'unknown')
            risk = p.risk_score if hasattr(p, 'risk_score') else p.get('risk_score', 0)
            lines.append(f"opendesk_predictive_agent_pod_risk_score{{pod=\"{pod_key}\"}} {risk}")
        # Per-pod detailed metrics (CPU trend, memory trend, restart count, state, confidence)
        if sm is not None:
            lines.append("# HELP opendesk_predictive_agent_pod_cpu_trend Per-pod CPU trend (millicores/min)")
            lines.append("# TYPE opendesk_predictive_agent_pod_cpu_trend gauge")
            lines.append("# HELP opendesk_predictive_agent_pod_mem_trend Per-pod memory trend (MiB/min)")
            lines.append("# TYPE opendesk_predictive_agent_pod_mem_trend gauge")
            lines.append("# HELP opendesk_predictive_agent_pod_restart_count Per-pod restart count")
            lines.append("# TYPE opendesk_predictive_agent_pod_restart_count gauge")
            lines.append("# HELP opendesk_predictive_agent_pod_state Per-pod state (0=healthy, 5=failed)")
            lines.append("# TYPE opendesk_predictive_agent_pod_state gauge")
            lines.append("# HELP opendesk_predictive_agent_kalman_confidence Per-pod Kalman filter confidence (0-1)")
            lines.append("# TYPE opendesk_predictive_agent_kalman_confidence gauge")
            lines.append("# HELP opendesk_predictive_agent_markov_transition Per-pod Markov transition probability")
            lines.append("# TYPE opendesk_predictive_agent_markov_transition gauge")
            for pod_key, tracker in sm.pods.items():
                cpu_trend = getattr(tracker, 'cpu_trend', 0.0) or 0.0
                mem_trend = getattr(tracker, 'memory_trend', 0.0) or 0.0
                restart_count = getattr(tracker, 'restart_count', 0)
                state_num = self._STATE_MAP.get(getattr(tracker, 'state', 'HEALTHY'), 0)
                # Find matching prediction for confidence and markov state
                confidence = 0.0
                markov_p = 0.0
                for p in predictions:
                    pk = p.pod_key if hasattr(p, 'pod_key') else p.get('pod_key', '')
                    if pk == pod_key:
                        confidence = p.confidence if hasattr(p, 'confidence') else p.get('confidence', 0.0)
                        markov_p = p.markov_p_critical if hasattr(p, 'markov_p_critical') else 0.0
                        break
                lines.append(f"opendesk_predictive_agent_pod_cpu_trend{{pod=\"{pod_key}\"}} {cpu_trend}")
                lines.append(f"opendesk_predictive_agent_pod_mem_trend{{pod=\"{pod_key}\"}} {mem_trend}")
                lines.append(f"opendesk_predictive_agent_pod_restart_count{{pod=\"{pod_key}\"}} {restart_count}")
                lines.append(f"opendesk_predictive_agent_pod_state{{pod=\"{pod_key}\"}} {state_num}")
                lines.append(f"opendesk_predictive_agent_kalman_confidence{{pod=\"{pod_key}\"}} {confidence}")
                lines.append(f"opendesk_predictive_agent_markov_transition{{pod=\"{pod_key}\"}} {markov_p}")
        # Remediation metrics (REM-8)
        rem = _context.remediation_manager
        if rem is not None:
            stats = rem.get_stats()
            lines.extend([
                "# HELP opendesk_predictive_agent_remediation_actions_total Total remediation actions",
                "# TYPE opendesk_predictive_agent_remediation_actions_total counter",
                f"opendesk_predictive_agent_remediation_actions_total {stats['total_actions']}",
                "# HELP opendesk_predictive_agent_remediation_successful_total Successful remediation actions",
                "# TYPE opendesk_predictive_agent_remediation_successful_total counter",
                f"opendesk_predictive_agent_remediation_successful_total {stats['successful_actions']}",
                "# HELP opendesk_predictive_agent_remediation_failed_total Failed remediation actions",
                "# TYPE opendesk_predictive_agent_remediation_failed_total counter",
                f"opendesk_predictive_agent_remediation_failed_total {stats['failed_actions']}",
                "# HELP opendesk_predictive_agent_remediation_dry_run_total Dry-run remediation actions",
                "# TYPE opendesk_predictive_agent_remediation_dry_run_total counter",
                f"opendesk_predictive_agent_remediation_dry_run_total {stats['dry_run_actions']}",
            ])
        return "\n".join(lines) + "\n"

    def _status_dict(self):
        sm = _context.state_model
        pred = _context.predictor
        pods_tracked = len(sm.pods) if sm is not None else 0
        predictions = _all_predictions(pred)
        predictions_count = len(predictions)
        at_risk = [p for p in predictions if p.risk_score >= (pred.risk_threshold if pred else 0.5)]
        uptime = int(time.time() - _context.start_time)
        return {
            "version": config.OPERATOR_VERSION,
            "operator": config.OPERATOR_NAME,
            "status": "running",
            "pod_count": pods_tracked,
            "predictions_count": predictions_count,
            "at_risk_count": len(at_risk),
            "uptime_seconds": uptime,
        }

    def _predictions_dict(self):
        predictions = [_serialize_prediction(p) for p in _all_predictions(_context.predictor)]
        # Sort by risk score descending (highest risk first)
        predictions.sort(key=lambda p: p.get("risk_score", 0), reverse=True)
        return {
            "predictions": predictions,
            "total": len(predictions),
        }

    def _state_dict(self):
        sm = _context.state_model
        if sm is not None:
            data = sm.to_dict()
            data["total_pods_tracked"] = len(sm.pods)
            # Expose markov chain info under a clear alias
            if "markov" in data:
                data["markov_chain"] = data["markov"]
            return data
        return {
            "pods": {},
            "states": "stable",
            "markov_chain": None,
            "total_pods_tracked": 0,
        }

    def _cache_dict(self):
        cache = _context.cache
        if cache is None:
            return {"cache": {}, "total": 0}
        if isinstance(cache, dict):
            return {"cache": cache, "total": len(cache)}
        try:
            items = dict(cache)
            return {"cache": items, "total": len(items)}
        except Exception:
            return {"cache": {}, "total": 0}

    def _reanalyze(self):
        """Trigger a reconcile cycle via callback, returning (data, status)."""
        cb = _context.reconcile_callback
        if cb is not None:
            try:
                result = cb()
                return {"status": "ok", "reanalyze": "triggered", "result": result}, 200
            except Exception as exc:  # noqa: BLE001 - surface error to caller
                return {"status": "error", "error": str(exc)}, 503
        return {"status": "ok", "reanalyze": "triggered"}, 200

    def _remediate_get(self):
        """GET /remediate — return remediation config, stats, and audit trail."""
        rem = _context.remediation_manager
        if rem is None:
            return {"status": "disabled", "message": "Remediation not initialized"}
        stats = rem.get_stats()
        audit_trail = rem.get_audit_trail(limit=50)
        return {
            "status": "enabled" if not rem.dry_run else "dry_run",
            "dry_run": rem.dry_run,
            "risk_threshold": rem.risk_threshold,
            "registered_actions": stats["registered_actions"],
            "stats": stats,
            "audit_trail": audit_trail,
            "safety_policy": {
                "max_per_minute": rem.safety_policy.max_per_minute,
                "max_per_hour": rem.safety_policy.max_per_hour,
                "cooldown_seconds": rem.safety_policy.cooldown_seconds,
                "protected_namespaces": list(rem.safety_policy.protected_namespaces),
            },
        }

    def _remediate_post(self):
        """POST /remediate — manually trigger remediation for a specific pod.

        Expects JSON body: {"pod_name": "namespace/pod", "risk_score": 85.0}
        """
        rem = _context.remediation_manager
        if rem is None:
            return {"status": "error", "error": "Remediation not initialized"}, 503

        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length) if content_length > 0 else b""
        try:
            payload = json.loads(body) if body else {}
        except json.JSONDecodeError:
            return {"status": "error", "error": "Invalid JSON body"}, 400

        pod_name = payload.get("pod_name", "")
        risk_score = float(payload.get("risk_score", 0.0))
        if not pod_name:
            return {"status": "error", "error": "pod_name is required"}, 400

        # Look up pod state from the state model
        sm = _context.state_model
        if sm is None or pod_name not in sm.pods:
            return {"status": "error", "error": f"Pod '{pod_name}' not found in state model"}, 404

        tracker = sm.pods[pod_name]
        results = rem.evaluate(tracker, None, risk_score)
        return {
            "status": "ok",
            "pod_name": pod_name,
            "risk_score": risk_score,
            "results": [
                {
                    "action": r.action,
                    "target": r.target,
                    "success": r.success,
                    "dry_run": r.dry_run,
                    "message": r.message,
                    "timestamp": r.timestamp,
                    "command": r.command,
                }
                for r in results
            ],
        }, 200

    def _notifications_get(self):
        """GET /notifications — return recent notification history."""
        notifier = _context.notifier
        if notifier is None:
            return {"notifications": [], "total": 0, "message": "Notifier not initialized"}
        history = notifier.get_history(limit=50)
        return {"notifications": history, "total": len(history)}

    def log_message(self, format, *args):
        # Suppress standard logging to keep test output clean
        return


# ─── Server wrapper ──────────────────────────────────────────────────────────
class HTTPServer(BaseHTTPServer):
    """Wrapper for the HTTP server to allow easier shutdown in tests."""

    allow_reuse_address = True

    def shutdown_server(self):
        self.shutdown()


def start_server(metrics_port, health_port, state_model=None, predictor=None,
                 cache=None, reconcile_callback=None, history=None,
                 remediation_manager=None, notifier=None):
    """Start the metrics/API and health HTTP servers.

    Args:
        metrics_port: Port for the metrics + API server.
        health_port: Port for the health probe server.
        state_model: Optional :class:`StateModel` serving real pod state.
        predictor: Optional :class:`Predictor` serving real predictions.
        cache: Optional LLM analysis cache (dict) for the ``/cache`` endpoint.
        reconcile_callback: Optional callable triggered by ``/reanalyze``.
        history: Optional list of analysis-history entries for ``/history``.
        remediation_manager: Optional :class:`RemediationManager` for /remediate.
        notifier: Optional :class:`NotificationManager` for /notifications.

    Returns:
        The metrics HTTPServer instance. Calling ``shutdown()`` on it stops
        both the metrics and health servers.
    """
    # Populate the shared context with real data sources
    _context.state_model = state_model
    _context.predictor = predictor
    _context.cache = cache
    _context.reconcile_callback = reconcile_callback
    _context.history = history if history is not None else []
    _context.remediation_manager = remediation_manager
    _context.notifier = notifier
    _context.start_time = time.time()

    # Metrics and API server (use HTTPServer subclass with allow_reuse_address)
    metrics_server = HTTPServer(("0.0.0.0", metrics_port), RequestHandler)
    metrics_thread = threading.Thread(target=metrics_server.serve_forever, daemon=True)
    metrics_thread.start()

    # Health server
    health_server = HTTPServer(("0.0.0.0", health_port), RequestHandler)
    health_thread = threading.Thread(target=health_server.serve_forever, daemon=True)
    health_thread.start()

    # Ensure shutting down the returned handle stops both servers
    original_shutdown = metrics_server.shutdown

    def shutdown_both():
        health_server.shutdown()
        original_shutdown()

    metrics_server.shutdown = shutdown_both

    return metrics_server
