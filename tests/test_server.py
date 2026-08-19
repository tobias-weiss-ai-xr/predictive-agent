"""Test HTTP server endpoints."""
import pytest
import json
import threading
import time
import urllib.request
import urllib.error
from dev_agent.server import HTTPServer, start_server


@pytest.fixture(scope="module")
def test_server():
    """Start test servers on alternate ports."""
    import dev_agent.config as config
    config.HEALTH_PORT = 18081
    config.METRICS_PORT = 18080

    metrics_server = start_server(18080, 18081)
    time.sleep(0.5)
    yield
    metrics_server.shutdown()


def test_healthz(test_server):
    """Test /healthz endpoint."""
    with urllib.request.urlopen("http://localhost:18081/healthz") as resp:
        data = json.loads(resp.read().decode())
        assert "status" in data


def test_ready(test_server):
    """Test /ready endpoint."""
    with urllib.request.urlopen("http://localhost:18081/ready") as resp:
        data = json.loads(resp.read().decode())
        assert "status" in data


def test_metrics(test_server):
    """Test /metrics endpoint."""
    with urllib.request.urlopen("http://localhost:18080/metrics") as resp:
        text = resp.read().decode()
        assert "opendesk_dev_agent" in text


def test_status(test_server):
    """Test /status endpoint."""
    with urllib.request.urlopen("http://localhost:18080/status") as resp:
        data = json.loads(resp.read().decode())
        assert "version" in data or "operator" in data


def test_predictions(test_server):
    """Test /predictions endpoint."""
    with urllib.request.urlopen("http://localhost:18080/predictions") as resp:
        data = json.loads(resp.read().decode())
        assert "predictions" in data
        assert "total" in data


def test_state(test_server):
    """Test /state endpoint."""
    with urllib.request.urlopen("http://localhost:18080/state") as resp:
        data = json.loads(resp.read().decode())
        assert "markov_chain" in data or "pods" in data or "states" in data


def test_history(test_server):
    """Test /history endpoint."""
    with urllib.request.urlopen("http://localhost:18080/history") as resp:
        data = json.loads(resp.read().decode())
        assert isinstance(data, list)


def test_404(test_server):
    """Test 404 for unknown endpoint."""
    try:
        urllib.request.urlopen("http://localhost:18080/nonexistent")
        pytest.fail("Should have raised 404")
    except urllib.error.HTTPError as e:
        assert e.code == 404
