"""Tests for DockerSocketClient and DockerCollector (docker backend)."""

import json
import os
import socket
import threading
import time

import pytest

from predictive_agent import config
from predictive_agent.collector_docker import DockerSocketClient, DockerCollector


def _start_fake_docker_server(socket_path, responder):
    """Serve a fake unix-socket HTTP responder in a background thread."""
    if os.path.exists(socket_path):
        os.unlink(socket_path)
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(socket_path)
    server.listen(16)

    def _serve():
        while True:
            try:
                conn, _ = server.accept()
            except OSError:
                return
            try:
                conn.settimeout(5)
                # Read the request line + headers
                request = b""
                while not request.endswith(b"\r\n\r\n"):
                    chunk = conn.recv(512)
                    if not chunk:
                        break
                    request += chunk
                method_line = request.split(b"\r\n", 1)[0].decode("utf-8", "replace")
                parts = method_line.split(" ")
                path = parts[1] if len(parts) >= 2 else "/"
                status, body = responder(path)
                payload = body.encode("utf-8") if isinstance(body, str) else body
                response = (
                    f"HTTP/1.1 {status} OK\r\n"
                    f"Content-Type: application/json\r\n"
                    f"Content-Length: {len(payload)}\r\n"
                    f"Connection: close\r\n\r\n"
                ).encode("utf-8") + payload
                conn.sendall(response)
            finally:
                conn.close()

    t = threading.Thread(target=_serve, daemon=True)
    t.start()
    return server, t


@pytest.fixture
def collector_env(tmp_path):
    """Point config.DOCKER_SOCKET at a temp path and reset cache."""
    sock = str(tmp_path / "docker.sock")
    old_socket = config.DOCKER_SOCKET
    config.DOCKER_SOCKET = sock
    yield sock
    config.DOCKER_SOCKET = old_socket


SAMPLE_CONTAINER = {
    "Id": "abc123def456",
    "Names": ["/monitoring_predictive-agent_1"],
    "Labels": {"com.docker.compose.project": "monitoring"},
    "State": "running",
}

SAMPLE_DETAILS = {
    "Id": "abc123def456",
    "HostConfig": {"Memory": 536870912},  # 512 MiB
    "RestartCount": 2,
}

SAMPLE_STATS = {
    "read": "2025-01-01T00:00:00.000000000Z",
    "cpu_stats": {
        "cpu_usage": {"total_usage": 2000},
        "system_cpu_usage": 10000,
        "online_cpus": 8,
    },
    "precpu_stats": {
        "cpu_usage": {"total_usage": 1000},
        "system_cpu_usage": 5000,
        "online_cpus": 8,
    },
    "memory_stats": {"usage": 1073741824},  # 1 GiB
}


class TestDockerSocketClient:
    def test_request_over_unix_socket(self, collector_env):
        sock = collector_env
        server, _ = _start_fake_docker_server(sock, lambda p: (200, '{"ping": true}'))
        try:
            client = DockerSocketClient(sock)
            status, headers, body = client.request("GET", "/_ping")
            assert status == 200
            assert json.loads(body) == {"ping": True}
            assert "Content-Type" in headers
        finally:
            server.close()
            if os.path.exists(sock):
                os.unlink(sock)

    def test_socket_not_found_raises(self, tmp_path):
        client = DockerSocketClient(str(tmp_path / "missing.sock"))
        with pytest.raises((FileNotFoundError, ConnectionError)):
            client.request("GET", "/_ping", timeout=2)


class TestDockerCollector:
    def test_collect_normalizes_records(self, collector_env):
        sock = collector_env

        def responder(path):
            if path.startswith("/info"):
                return 200, json.dumps({"MemTotal": 17179869184})
            if path.startswith("/containers/json"):
                return 200, json.dumps([SAMPLE_CONTAINER])
            if path.startswith("/containers/abc123def456/json"):
                return 200, json.dumps(SAMPLE_DETAILS)
            if path.startswith("/containers/abc123def456/stats"):
                return 200, json.dumps(SAMPLE_STATS)
            if path.startswith("/containers/abc123def456/logs"):
                return 200, b"2025-01-01 INFO: ok\n"
            return 200, "{}"

        server, _ = _start_fake_docker_server(sock, responder)
        try:
            old_watch = config.WATCH_NAMESPACES
            config.WATCH_NAMESPACES = {"monitoring"}
            try:
                collector = DockerCollector(sock)
                records = collector.collect()
            finally:
                config.WATCH_NAMESPACES = old_watch
        finally:
            collector.close()
            server.close()
            if os.path.exists(sock):
                os.unlink(sock)

        assert "monitoring/predictive-agent" in records
        rec = records["monitoring/predictive-agent"]
        assert rec["namespace"] == "monitoring"
        assert rec["name"] == "predictive-agent"
        assert rec["memory_limit_mib"] == 512
        assert rec["restart_count"] == 2
        assert rec["node_pressure"] is False
        assert isinstance(rec["cpu_m"], int)
        assert isinstance(rec["log_errors"], int)

    def test_collect_filters_unwatched_namespace(self, collector_env):
        sock = collector_env
        other = dict(SAMPLE_CONTAINER)
        other["Names"] = ["/other_stack_web_1"]
        other["Labels"] = {"com.docker.compose.project": "other"}

        def responder(path):
            if path.startswith("/containers/json"):
                return 200, json.dumps([SAMPLE_CONTAINER, other])
            return 200, "{}"

        server, _ = _start_fake_docker_server(sock, responder)
        try:
            old_watch = config.WATCH_NAMESPACES
            config.WATCH_NAMESPACES = {"monitoring"}
            try:
                collector = DockerCollector(sock)
                records = collector.collect()
            finally:
                config.WATCH_NAMESPACES = old_watch
        finally:
            collector.close()
            server.close()
            if os.path.exists(sock):
                os.unlink(sock)

        assert "monitoring/predictive-agent" in records
        assert "other_stack/web" not in records

    def test_socket_unavailable_returns_empty(self, tmp_path):
        old_socket = config.DOCKER_SOCKET
        config.DOCKER_SOCKET = str(tmp_path / "nope.sock")
        try:
            records = DockerCollector(str(tmp_path / "nope.sock")).collect()
            assert records == {}
        finally:
            config.DOCKER_SOCKET = old_socket

    def test_count_log_errors(self, collector_env):
        collector = DockerCollector(collector_env)
        assert collector.count_log_errors("ok line") == 0
        assert collector.count_log_errors("ERROR: boom") == 1
        assert collector.count_log_errors("Traceback (most recent call last)") == 1
        assert collector.count_log_errors("panic: nil pointer") == 1