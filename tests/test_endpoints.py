"""Test HTTP endpoints."""
import pytest
import json
import threading
import time
import urllib.request
from dev_agent import start_test_server


@pytest.fixture(scope="module")
def test_server():
    """Start test server for endpoint testing."""
    server_thread = threading.Thread(target=start_test_server, daemon=True)
    server_thread.start()
    time.sleep(1)  # Give server time to start
    yield
    # Server will stop when thread ends


def test_predictions_endpoint(test_server):
    """Test /predictions endpoint."""
    try:
        with urllib.request.urlopen("http://localhost:8080/predictions") as resp:
            data = json.loads(resp.read().decode())
            assert "predictions" in data
            assert "total" in data
            assert "timestamp" in data
    except urllib.error.URLError as e:
        pytest.fail(f"Failed to connect to test server: {e}")


def test_state_endpoint(test_server):
    """Test /state endpoint."""
    try:
        with urllib.request.urlopen("http://localhost:8080/state") as resp:
            data = json.loads(resp.read().decode())
            assert "markov_chain" in data
            assert "pod_states" in data
            assert "total_pods_tracked" in data
    except urllib.error.URLError as e:
        pytest.fail(f"Failed to connect to test server: {e}")


def test_health_endpoints(test_server):
    """Test health endpoints still work."""
    # Test /healthz
    with urllib.request.urlopen("http://localhost:8081/healthz") as resp:
        data = json.loads(resp.read().decode())
        assert "status" in data
    
    # Test /ready
    with urllib.request.urlopen("http://localhost:8081/ready") as resp:
        data = json.loads(resp.read().decode())
        assert "status" in data
    
    # Test /metrics
    with urllib.request.urlopen("http://localhost:8080/metrics") as resp:
        text = resp.read().decode()
        assert "opendesk_dev_agent" in text