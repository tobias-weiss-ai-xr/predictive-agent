import json
import threading
from http.server import HTTPServer as BaseHTTPServer, BaseHTTPRequestHandler
from dev_agent import config

class RequestHandler(BaseHTTPRequestHandler):
    def _send_response(self, data, status=200, content_type="application/json"):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.end_headers()
        if isinstance(data, (dict, list)):
            self.wfile.write(json.dumps(data).encode())
        else:
            self.wfile.write(data.encode())

    def do_GET(self):
        if self.path == "/healthz":
            self._send_response({"status": "ok"})
        elif self.path == "/ready":
            self._send_response({"status": "ready"})
        elif self.path == "/metrics":
            # Prometheus format
            metrics = "opendesk_dev_agent_uptime_seconds 0\n"
            self._send_response(metrics, content_type="text/plain")
        elif self.path == "/status":
            self._send_response({
                "version": "4.0",
                "operator": "dev-agent-monitor",
                "status": "running"
            })
        elif self.path == "/predictions":
            self._send_response({
                "predictions": [],
                "total": 0
            })
        elif self.path == "/state":
            self._send_response({
                "pods": {},
                "states": "stable"
            })
        elif self.path == "/history":
            self._send_response([])
        else:
            self._send_response({"error": "Not Found"}, status=404)

    def log_message(self, format, *args):
        # Suppress standard logging to keep test output clean
        return

class HTTPServer(BaseHTTPServer):
    """Wrapper for the HTTP server to allow easier shutdown in tests."""
    def shutdown_server(self):
        self.shutdown()

def start_server(metrics_port, health_port):
    """
    Starts two HTTP servers: one for metrics/API and one for health checks.
    Returns the metrics server instance.
    """
    # Metrics and API server
    metrics_handler = RequestHandler
    metrics_server = BaseHTTPServer(("0.0.0.0", metrics_port), metrics_handler)
    metrics_thread = threading.Thread(target=metrics_server.serve_forever, daemon=True)
    metrics_thread.start()

    # Health server
    health_handler = RequestHandler
    health_server = BaseHTTPServer(("0.0.0.0", health_port), health_handler)
    health_thread = threading.Thread(target=health_server.serve_forever, daemon=True)
    health_thread.start()

    # The tests expect a server object that has a shutdown() method.
    # BaseHTTPServer.shutdown() exists, but we need to return one of them.
    # We return metrics_server as the primary handle.
    
    # To ensure both can be shut down if needed, we could wrap them, 
    # but the tests specifically call shutdown() on the returned object.
    # We will monkeypatch shutdown to kill both for convenience in tests.
    
    original_shutdown = metrics_server.shutdown
    def shutdown_both():
        health_server.shutdown()
        original_shutdown()
    
    metrics_server.shutdown = shutdown_both
    
    return metrics_server
