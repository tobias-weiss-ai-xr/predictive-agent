"""Tests for ContainerRestartAction (docker-mode remediation)."""

import json
import os
import socket
import threading

import pytest
from unittest.mock import MagicMock

from predictive_agent import config
from predictive_agent.remediator import ActionContext, RemediationResult
from predictive_agent.actions.container_restart import (
    ContainerRestartAction,
    DockerSocketClient,
)


class TestContainerRestartShouldExecute:
    """Unit tests for the decision logic (no socket needed)."""

    def _make_pod_state(self, namespace="monitoring", restart_count=0, running=True):
        return MagicMock(namespace=namespace, restart_count=restart_count, running=running)

    def _make_action(self):
        return ContainerRestartAction()

    def test_high_risk_and_many_restarts_triggers(self):
        action = self._make_action()
        pod = self._make_pod_state(restart_count=7)
        assert action.should_execute(pod, None, 90.0) is True

    def test_high_risk_running_few_restarts_does_not_trigger(self):
        action = self._make_action()
        pod = self._make_pod_state(restart_count=2, running=True)
        assert action.should_execute(pod, None, 90.0) is False

    def test_low_risk_does_not_trigger_even_with_restarts(self):
        action = self._make_action()
        pod = self._make_pod_state(restart_count=10)
        assert action.should_execute(pod, None, 50.0) is False

    def test_protected_namespace_blocks(self):
        action = self._make_action()
        pod = self._make_pod_state(namespace="kube-system", restart_count=10)
        assert action.should_execute(pod, None, 90.0) is False

    def test_risk_at_threshold_boundary_does_not_trigger(self):
        action = self._make_action()
        pod = self._make_pod_state(restart_count=10)
        threshold = float(os.environ.get("REMEDIATION_RISK_THRESHOLD", "70.0"))
        assert action.should_execute(pod, None, threshold) is False


class TestContainerRestartExecute:
    """End-to-end tests using a fake unix-socket Docker responder."""

    def _serve(self, socket_path, responder):
        if os.path.exists(socket_path):
            os.unlink(socket_path)
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(socket_path)
        server.listen(16)

        def _run():
            while True:
                try:
                    conn, _ = server.accept()
                except OSError:
                    return
                try:
                    conn.settimeout(3)
                    request = b""
                    while not request.endswith(b"\r\n\r\n"):
                        chunk = conn.recv(256)
                        if not chunk:
                            break
                        request += chunk
                    line = request.split(b"\r\n", 1)[0].decode("utf-8", "replace")
                    method = line.split(" ", 1)[0]
                    path = line.split(" ", 2)[1] if len(line.split(" ")) > 1 else "/"
                    call = (method, path)
                    status, body = responder(call)
                    payload = json.dumps(body).encode("utf-8")
                    resp = (
                        f"HTTP/1.1 {status} OK\r\n"
                        f"Content-Length: {len(payload)}\r\n\r\n"
                    ).encode("utf-8") + payload
                    conn.sendall(resp)
                except Exception:
                    pass
                finally:
                    conn.close()

        t = threading.Thread(target=_run, daemon=True)
        t.start()
        return server, t

    def test_dry_run_returns_result_without_calling_socket(self, tmp_path):
        calls = []
        action = ContainerRestartAction(DockerSocketClient(str(tmp_path / "never.sock")))
        ctx = ActionContext(namespace="monitoring", dry_run=True)
        result = action.execute("predictive-agent", ctx)
        assert result.success is True
        assert result.dry_run is True
        assert "dry run" in result.message
        assert calls == []

    def test_execute_restarts_container(self, tmp_path, monkeypatch):
        sock = str(tmp_path / "docker.sock")
        restarted = []

        def responder(call):
            method, path = call
            if method == "GET" and path.startswith("/containers/json"):
                return 200, [
                    {
                        "Id": "abc123",
                        "Names": ["/monitoring_predictive-agent_1"],
                    }
                ]
            if method == "POST" and "/restart" in path:
                restarted.append(path)
                return 204, {}
            return 404, {}

        server, _ = self._serve(sock, responder)

        class _Client(DockerSocketClient):
            def __init__(self):
                super().__init__(sock)

        try:
            action = ContainerRestartAction(_Client())
            ctx = ActionContext(namespace="monitoring", dry_run=False)
            result = action.execute("predictive-agent", ctx)
        finally:
            server.close()
            if os.path.exists(sock):
                os.unlink(sock)

        assert result.success is True
        assert len(restarted) == 1
        assert "/containers/abc123/restart" in restarted[0]

    def test_container_not_found_returns_failure(self, tmp_path):
        sock = str(tmp_path / "docker.sock")
        server, _ = self._serve(sock, lambda call: (200, []))
        try:
            action = ContainerRestartAction(DockerSocketClient(sock))
            ctx = ActionContext(namespace="monitoring", dry_run=False)
            result = action.execute("missing-container", ctx)
        finally:
            server.close()
            if os.path.exists(sock):
                os.unlink(sock)

        assert result.success is False
        assert "not found" in result.message

    def test_socket_missing_returns_failure_not_exception(self, tmp_path):
        action = ContainerRestartAction(DockerSocketClient(str(tmp_path / "nope.sock")))
        ctx = ActionContext(namespace="monitoring", dry_run=False)
        result = action.execute("predictive-agent", ctx)
        assert result.success is False
        assert isinstance(result, RemediationResult)