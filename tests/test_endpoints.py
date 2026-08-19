"""Test HTTP endpoints (integration tests with real server)."""
import pytest
import json
import time
import urllib.request
import urllib.error
from predictive_agent.server import start_server


@pytest.fixture(scope="module")
def test_server():
    """Start test server for endpoint testing."""
    metrics_server = start_server(18082, 18083)
    time.sleep(0.5)
    yield
    metrics_server.shutdown()


def test_predictions_endpoint(test_server):
    """Test /predictions endpoint."""
    try:
        with urllib.request.urlopen("http://localhost:18082/predictions") as resp:
            data = json.loads(resp.read().decode())
            assert "predictions" in data
            assert "total" in data
    except urllib.error.URLError as e:
        pytest.fail(f"Failed to connect to test server: {e}")


def test_state_endpoint(test_server):
    """Test /state endpoint."""
    try:
        with urllib.request.urlopen("http://localhost:18082/state") as resp:
            data = json.loads(resp.read().decode())
            assert "pods" in data or "markov_chain" in data or "states" in data
    except urllib.error.URLError as e:
        pytest.fail(f"Failed to connect to test server: {e}")


def test_health_endpoints(test_server):
    """Test health endpoints still work."""
    # Test /healthz
    with urllib.request.urlopen("http://localhost:18083/healthz") as resp:
        data = json.loads(resp.read().decode())
        assert "status" in data

    # Test /ready
    with urllib.request.urlopen("http://localhost:18083/ready") as resp:
        data = json.loads(resp.read().decode())
        assert "status" in data

    # Test /metrics
    with urllib.request.urlopen("http://localhost:18082/metrics") as resp:
        text = resp.read().decode()
        assert "opendesk_predictive_agent" in text
