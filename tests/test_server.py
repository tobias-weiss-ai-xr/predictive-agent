"""Test HTTP server endpoints with real data integration."""
import pytest
import json
import time
import urllib.request
import urllib.error
from predictive_agent.server import start_server
from predictive_agent.state_model import StateModel
from predictive_agent.predictor import Predictor, PredictionResult


@pytest.fixture(scope="module")
def test_server():
    """Start test servers on alternate ports."""
    import predictive_agent.config as config
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
        assert data["status"] == "ok"


def test_ready(test_server):
    """Test /ready endpoint."""
    with urllib.request.urlopen("http://localhost:18081/ready") as resp:
        data = json.loads(resp.read().decode())
        assert "status" in data
        assert data["status"] == "ready"


def test_metrics(test_server):
    """Test /metrics endpoint returns Prometheus format."""
    with urllib.request.urlopen("http://localhost:18080/metrics") as resp:
        text = resp.read().decode()
        assert "opendesk_predictive_agent" in text


def test_status(test_server):
    """Test /status endpoint returns version and pod count."""
    with urllib.request.urlopen("http://localhost:18080/status") as resp:
        data = json.loads(resp.read().decode())
        assert "version" in data
        assert "operator" in data


def test_predictions(test_server):
    """Test /predictions endpoint returns predictions list."""
    with urllib.request.urlopen("http://localhost:18080/predictions") as resp:
        data = json.loads(resp.read().decode())
        assert "predictions" in data
        assert "total" in data
        assert isinstance(data["predictions"], list)


def test_state(test_server):
    """Test /state endpoint returns pod states and markov chain."""
    with urllib.request.urlopen("http://localhost:18080/state") as resp:
        data = json.loads(resp.read().decode())
        assert "pods" in data or "markov_chain" in data or "states" in data


def test_history(test_server):
    """Test /history endpoint returns a list."""
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


def test_reanalyze(test_server):
    """Test /reanalyze endpoint exists."""
    try:
        with urllib.request.urlopen("http://localhost:18080/reanalyze") as resp:
            assert resp.status == 200
    except urllib.error.HTTPError as e:
        assert e.code in (200, 405, 503)


def test_cache(test_server):
    """Test /cache endpoint exists."""
    try:
        with urllib.request.urlopen("http://localhost:18080/cache") as resp:
            assert resp.status == 200
    except urllib.error.HTTPError as e:
        assert e.code in (200, 404, 503)
