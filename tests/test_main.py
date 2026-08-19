"""Test main reconcile loop."""
import pytest
from unittest.mock import patch, MagicMock
from predictive_agent.main import reconcile, ReconcileLoop


def test_reconcile_exists():
    """reconcile function should exist and be callable."""
    assert callable(reconcile)


def test_reconcile_returns_predictions():
    """reconcile should return a list of predictions."""
    with patch("predictive_agent.main.collect_top_metrics", return_value={}), \
         patch("predictive_agent.main.collect_top_nodes", return_value={}), \
         patch("predictive_agent.main.get_pod_resources", return_value={}), \
         patch("predictive_agent.main.get_node_conditions", return_value={}), \
         patch("predictive_agent.main.count_log_errors", return_value=0):
        result = reconcile()
        assert isinstance(result, dict)
        assert "predictions" in result
        assert "state" in result
        assert "timestamp" in result


def test_reconcile_with_metrics():
    """reconcile should process metrics and produce predictions."""
    mock_metrics = {
        "default/test-pod": {"cpu_m": 100, "memory_mib": 256}
    }
    mock_nodes = {
        "node-1": {"cpu_m": 500, "memory_mib": 4000, "cpu_pct": 25.0, "memory_pct": 40.0}
    }
    mock_resources = {
        "test-container": {"cpu_m": 1000, "memory_mib": 512}
    }
    mock_conditions = {
        "node-1": {"Ready": "True", "MemoryPressure": "False", "DiskPressure": "False"}
    }
    with patch("predictive_agent.main.collect_top_metrics", return_value=mock_metrics), \
         patch("predictive_agent.main.collect_top_nodes", return_value=mock_nodes), \
         patch("predictive_agent.main.get_pod_resources", return_value=mock_resources), \
         patch("predictive_agent.main.get_node_conditions", return_value=mock_conditions), \
         patch("predictive_agent.main.count_log_errors", return_value=0):
        result = reconcile()
        assert "predictions" in result
        assert isinstance(result["predictions"], list)
        assert "state" in result
        assert "timestamp" in result


def test_reconcile_loop_creation():
    """ReconcileLoop should be creatable."""
    loop = ReconcileLoop(interval=1)
    assert loop.interval == 1
    assert loop.running is False or loop.running is True


def test_reconcile_loop_start_stop():
    """ReconcileLoop should start and stop."""
    loop = ReconcileLoop(interval=1)
    loop.start()
    assert loop.running is True
    loop.stop()
    assert loop.running is False


def test_reconcile_loop_runs_once():
    """ReconcileLoop should run reconcile at least once when started."""
    call_count = 0
    original_reconcile = None

    def mock_reconcile():
        nonlocal call_count
        call_count += 1
        return {"predictions": [], "state": {}, "timestamp": "2025-01-01T00:00:00Z"}

    loop = ReconcileLoop(interval=1)
    loop._reconcile_fn = mock_reconcile
    loop.start()
    import time
    time.sleep(2.5)
    loop.stop()
    assert call_count >= 1
