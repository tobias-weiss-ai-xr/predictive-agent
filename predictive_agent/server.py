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
    )

    def __init__(self):
        self.state_model = None
        self.predictor = None
        self.cache = None
        self.reconcile_callback = None
        self.history = []
        self.start_time = time.time()


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
        else:
            self._send_response({"error": "Not Found"}, status=404)

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if path == "/reanalyze":
            data, status = self._reanalyze()
            self._send_response(data, status=status)
        else:
            self._send_response({"error": "Not Found"}, status=404)

    # ── Endpoint payloads ────────────────────────────────────────────────────
    def _metrics_text(self):
        """Prometheus-format metrics with real gauges from state/predictor."""
        sm = _context.state_model
        pred = _context.predictor

        pods_tracked = len(sm.pods) if sm is not None else 0
        predictions = _all_predictions(pred)
        predictions_count = len(predictions)
        risk_score = max((p.risk_score for p in predictions), default=0.0)
        uptime = int(time.time() - _context.start_time)

        lines = [
            "# HELP opendesk_predictive_agent_pods_tracked Number of pods currently tracked",
            "# TYPE opendesk_predictive_agent_pods_tracked gauge",
            f"opendesk_predictive_agent_pods_tracked {pods_tracked}",
            "# HELP opendesk_predictive_agent_predictions_count Number of active predictions",
            "# TYPE opendesk_predictive_agent_predictions_count gauge",
            f"opendesk_predictive_agent_predictions_count {predictions_count}",
            "# HELP opendesk_predictive_agent_risk_score Highest current risk score (0-1)",
            "# TYPE opendesk_predictive_agent_risk_score gauge",
            f"opendesk_predictive_agent_risk_score {risk_score}",
            "# HELP opendesk_predictive_agent_uptime_seconds Uptime in seconds",
            "# TYPE opendesk_predictive_agent_uptime_seconds gauge",
            f"opendesk_predictive_agent_uptime_seconds {uptime}",
            "# HELP opendesk_dev_agent_pods_tracked Number of pods currently tracked",
            "# TYPE opendesk_dev_agent_pods_tracked gauge",
            f"opendesk_dev_agent_pods_tracked {pods_tracked}",
            "# HELP opendesk_dev_agent_predictions_count Number of active predictions",
            "# TYPE opendesk_dev_agent_predictions_count gauge",
            f"opendesk_dev_agent_predictions_count {predictions_count}",
            "# HELP opendesk_dev_agent_risk_score Highest current risk score (0-1)",
            "# TYPE opendesk_dev_agent_risk_score gauge",
            f"opendesk_dev_agent_risk_score {risk_score}",
        ]
        return "\n".join(lines) + "\n"

    def _status_dict(self):
        sm = _context.state_model
        pred = _context.predictor
        pods_tracked = len(sm.pods) if sm is not None else 0
        predictions_count = len(_all_predictions(pred))
        return {
            "version": config.OPERATOR_VERSION,
            "operator": config.OPERATOR_NAME,
            "status": "running",
            "pod_count": pods_tracked,
            "predictions_count": predictions_count,
        }

    def _predictions_dict(self):
        predictions = [_serialize_prediction(p) for p in _all_predictions(_context.predictor)]
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

    def log_message(self, format, *args):
        # Suppress standard logging to keep test output clean
        return


# ─── Server wrapper ──────────────────────────────────────────────────────────
class HTTPServer(BaseHTTPServer):
    """Wrapper for the HTTP server to allow easier shutdown in tests."""

    def shutdown_server(self):
        self.shutdown()


def start_server(metrics_port, health_port, state_model=None, predictor=None,
                 cache=None, reconcile_callback=None, history=None):
    """Start the metrics/API and health HTTP servers.

    Args:
        metrics_port: Port for the metrics + API server.
        health_port: Port for the health probe server.
        state_model: Optional :class:`StateModel` serving real pod state.
        predictor: Optional :class:`Predictor` serving real predictions.
        cache: Optional LLM analysis cache (dict) for the ``/cache`` endpoint.
        reconcile_callback: Optional callable triggered by ``/reanalyze``.
        history: Optional list of analysis-history entries for ``/history``.

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
    _context.start_time = time.time()

    # Metrics and API server
    metrics_server = BaseHTTPServer(("0.0.0.0", metrics_port), RequestHandler)
    metrics_thread = threading.Thread(target=metrics_server.serve_forever, daemon=True)
    metrics_thread.start()

    # Health server
    health_server = BaseHTTPServer(("0.0.0.0", health_port), RequestHandler)
    health_thread = threading.Thread(target=health_server.serve_forever, daemon=True)
    health_thread.start()

    # Ensure shutting down the returned handle stops both servers
    original_shutdown = metrics_server.shutdown

    def shutdown_both():
        health_server.shutdown()
        original_shutdown()

    metrics_server.shutdown = shutdown_both

    return metrics_server
