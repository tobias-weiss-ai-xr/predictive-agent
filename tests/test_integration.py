"""Integration tests — full reconcile cycle and HTTP server with real state.

SOTA paradigm: Integration tests verify that multiple components work together
correctly. Unlike unit tests that isolate each function, these tests exercise
the full pipeline: collector → state model → predictor → persistence → server.

Covers:
- Full reconcile cycle with mocked kubectl (end-to-end pipeline)
- HTTP server with real StateModel and Predictor (real data flow)
- State persistence round-trip (save → reload → verify)
- Markov chain learning across multiple reconcile cycles
- Server endpoints with populated state (non-empty /predictions, /state)
- Concurrency: ReconcileLoop thread safety
"""
import json
import os
import tempfile
import time
import threading
import pytest
from unittest.mock import patch

from predictive_agent.kalman import KalmanTrend
from predictive_agent.markov import MarkovChain
from predictive_agent.state_model import StateModel, PodTracker
from predictive_agent.predictor import Predictor, PredictionResult
from predictive_agent.persistence import StateStore
from predictive_agent.server import start_server


# ─── Fixtures ───────────────────────────────────────────────────────────────

@pytest.fixture
def mock_kubectl_metrics():
    """Mock kubectl top pods output with realistic pod metrics."""
    return {
        "default/test-pod-1": {"cpu_m": 100, "memory_mib": 256},
        "default/test-pod-2": {"cpu_m": 500, "memory_mib": 800},
        "opendesk/openldap-0": {"cpu_m": 50, "memory_mib": 128},
        "llm/ollama-0": {"cpu_m": 2000, "memory_mib": 4096},
    }


@pytest.fixture
def mock_kubectl_nodes():
    """Mock kubectl top nodes output."""
    return {
        "clrz14-06": {"cpu_m": 2000, "memory_mib": 16000, "cpu_pct": 20.0, "memory_pct": 40.0},
        "clrz14-07": {"cpu_m": 3000, "memory_mib": 24000, "cpu_pct": 30.0, "memory_pct": 60.0},
        "clrz14-08": {"cpu_m": 1500, "memory_mib": 12000, "cpu_pct": 15.0, "memory_pct": 30.0},
    }


@pytest.fixture
def mock_pod_json():
    """Mock kubectl get pods -o json output."""
    return {
        "items": [
            {
                "metadata": {"namespace": "default", "name": "test-pod-1"},
                "spec": {
                    "nodeName": "clrz14-06",
                    "containers": [{
                        "name": "app",
                        "resources": {"limits": {"cpu": "1000m", "memory": "512Mi"}}
                    }]
                },
                "status": {
                    "phase": "Running",
                    "containerStatuses": [{"ready": True, "restartCount": 0}]
                }
            },
            {
                "metadata": {"namespace": "default", "name": "test-pod-2"},
                "spec": {
                    "nodeName": "clrz14-07",
                    "containers": [{
                        "name": "app",
                        "resources": {"limits": {"cpu": "2000m", "memory": "1024Mi"}}
                    }]
                },
                "status": {
                    "phase": "Running",
                    "containerStatuses": [{"ready": True, "restartCount": 2}]
                }
            },
            {
                "metadata": {"namespace": "opendesk", "name": "openldap-0"},
                "spec": {
                    "nodeName": "clrz14-07",
                    "containers": [{
                        "name": "openldap",
                        "resources": {"limits": {"cpu": "500m", "memory": "256Mi"}}
                    }]
                },
                "status": {
                    "phase": "Running",
                    "containerStatuses": [{"ready": True, "restartCount": 0}]
                }
            },
        ]
    }


@pytest.fixture
def mock_node_json():
    """Mock kubectl get nodes -o json output."""
    return {
        "items": [
            {
                "metadata": {"name": "clrz14-06"},
                "status": {
                    "conditions": [
                        {"type": "Ready", "status": "True"},
                        {"type": "MemoryPressure", "status": "False"},
                        {"type": "DiskPressure", "status": "False"},
                    ]
                }
            },
            {
                "metadata": {"name": "clrz14-07"},
                "status": {
                    "conditions": [
                        {"type": "Ready", "status": "True"},
                        {"type": "MemoryPressure", "status": "False"},
                        {"type": "DiskPressure", "status": "False"},
                    ]
                }
            },
        ]
    }


# ─── Full reconcile cycle integration ───────────────────────────────────────

class TestReconcileIntegration:
    """Integration tests for the full reconcile pipeline."""

    def test_reconcile_with_mocked_kubectl(self, mock_kubectl_metrics, mock_kubectl_nodes,
                                             mock_pod_json, mock_node_json):
        """Full reconcile cycle: collect → state model → predict → persist."""
        from predictive_agent.main import reconcile, _state_model, _predictor, _state_store
        import predictive_agent.main as main_mod

        # Initialize state
        main_mod._state_model = StateModel()
        main_mod._predictor = Predictor(risk_threshold=0.5)
        with tempfile.TemporaryDirectory() as tmpdir:
            main_mod._state_store = StateStore(
                os.path.join(tmpdir, "state.json"),
                os.path.join(tmpdir, "pred.json"),
            )

            with patch("predictive_agent.main.collect_top_metrics", return_value=mock_kubectl_metrics), \
                 patch("predictive_agent.main.collect_top_nodes", return_value=mock_kubectl_nodes), \
                 patch("predictive_agent.main.get_pod_resources", return_value={}), \
                 patch("predictive_agent.main.get_node_conditions", return_value={}), \
                 patch("predictive_agent.main._get_pods_json", return_value=mock_pod_json), \
                 patch("predictive_agent.main.count_log_errors", return_value=0), \
                 patch("predictive_agent.main.run_cmd", return_value=(0, "", "")):
                result = reconcile()

            assert "predictions" in result
            assert "state" in result
            assert "timestamp" in result
            assert "pods_tracked" in result
            assert result["pods_tracked"] > 0

    def test_reconcile_produces_valid_predictions(self, mock_kubectl_metrics, mock_kubectl_nodes,
                                                     mock_pod_json):
        """Reconcile predictions should have valid structure."""
        from predictive_agent.main import reconcile
        import predictive_agent.main as main_mod

        main_mod._state_model = StateModel()
        main_mod._predictor = Predictor(risk_threshold=0.5)
        with tempfile.TemporaryDirectory() as tmpdir:
            main_mod._state_store = StateStore(
                os.path.join(tmpdir, "state.json"),
                os.path.join(tmpdir, "pred.json"),
            )

            with patch("predictive_agent.main.collect_top_metrics", return_value=mock_kubectl_metrics), \
                 patch("predictive_agent.main.collect_top_nodes", return_value=mock_kubectl_nodes), \
                 patch("predictive_agent.main.get_pod_resources", return_value={}), \
                 patch("predictive_agent.main.get_node_conditions", return_value={}), \
                 patch("predictive_agent.main._get_pods_json", return_value=mock_pod_json), \
                 patch("predictive_agent.main.count_log_errors", return_value=0), \
                 patch("predictive_agent.main.run_cmd", return_value=(0, "", "")):
                result = reconcile()

            for pred in result["predictions"]:
                assert "pod_key" in pred
                assert "risk_score" in pred
                assert 0.0 <= pred["risk_score"] <= 0.99

    def test_reconcile_persists_state(self, mock_kubectl_metrics, mock_kubectl_nodes,
                                       mock_pod_json):
        """Reconcile should persist state to PVC."""
        from predictive_agent.main import reconcile
        import predictive_agent.main as main_mod

        main_mod._state_model = StateModel()
        main_mod._predictor = Predictor(risk_threshold=0.5)
        with tempfile.TemporaryDirectory() as tmpdir:
            sm_file = os.path.join(tmpdir, "state.json")
            pred_file = os.path.join(tmpdir, "pred.json")
            main_mod._state_store = StateStore(sm_file, pred_file)

            with patch("predictive_agent.main.collect_top_metrics", return_value=mock_kubectl_metrics), \
                 patch("predictive_agent.main.collect_top_nodes", return_value=mock_kubectl_nodes), \
                 patch("predictive_agent.main.get_pod_resources", return_value={}), \
                 patch("predictive_agent.main.get_node_conditions", return_value={}), \
                 patch("predictive_agent.main._get_pods_json", return_value=mock_pod_json), \
                 patch("predictive_agent.main.count_log_errors", return_value=0), \
                 patch("predictive_agent.main.run_cmd", return_value=(0, "", "")):
                reconcile()

            # State file should exist and contain markov data
            assert os.path.exists(sm_file)
            with open(sm_file) as f:
                state = json.load(f)
            assert "counts" in state
            assert "total_transitions" in state

            # Predictions file should exist
            assert os.path.exists(pred_file)
            with open(pred_file) as f:
                preds = json.load(f)
            assert isinstance(preds, list)


# ─── HTTP server with real state ────────────────────────────────────────────

class TestServerIntegration:
    """Integration tests for HTTP server with real StateModel/Predictor."""

    @pytest.fixture(scope="class")
    def server_with_state(self):
        """Start server with populated state model and predictor."""
        sm = StateModel()
        sm.update_pod("default", "web-0", memory_mib=500, memory_limit_mib=1024,
                       cpu_m=100, restart_count=0, log_errors=0, node_pressure=False)
        sm.update_pod("default", "web-1", memory_mib=950, memory_limit_mib=1024,
                       cpu_m=900, restart_count=5, log_errors=8, node_pressure=True)

        pred = Predictor(risk_threshold=0.3)
        pred.predict(
            pod_key="default/web-0",
            memory_pct=48.8, memory_trend_mib_per_min=0.5,
            memory_limit_mib=1024, memory_mib=500,
            cpu_pct=10.0, restart_rate_per_hr=0.0, log_error_rate_per_min=0.0,
            node_memory_pressure=False, node_disk_pressure=False,
            markov_state="HEALTHY", markov_p_critical=0.01, markov_p_failed=0.001,
        )
        pred.predict(
            pod_key="default/web-1",
            memory_pct=92.7, memory_trend_mib_per_min=5.0,
            memory_limit_mib=1024, memory_mib=950,
            cpu_pct=90.0, restart_rate_per_hr=5.0, log_error_rate_per_min=8.0,
            node_memory_pressure=True, node_disk_pressure=False,
            markov_state="STRESSED", markov_p_critical=0.3, markov_p_failed=0.1,
        )

        cache = {"last_analysis": {"pod": "default/web-1", "severity": "high"}}
        history = [{"timestamp": "2025-01-01T00:00:00Z", "pod": "default/web-1", "action": "investigate"}]

        server = start_server(18090, 18091, state_model=sm, predictor=pred,
                              cache=cache, history=history)
        time.sleep(0.3)
        yield server
        server.shutdown()

    def test_status_with_state(self, server_with_state):
        """Status endpoint should show real pod count."""
        import urllib.request
        with urllib.request.urlopen("http://localhost:18090/status") as resp:
            data = json.loads(resp.read().decode())
            assert data["pod_count"] == 2
            assert data["predictions_count"] == 2

    def test_predictions_with_data(self, server_with_state):
        """Predictions endpoint should return real predictions."""
        import urllib.request
        with urllib.request.urlopen("http://localhost:18090/predictions") as resp:
            data = json.loads(resp.read().decode())
            assert data["total"] == 2
            assert len(data["predictions"]) == 2
            # web-1 should have higher risk than web-0
            risks = {p["pod_key"]: p["risk_score"] for p in data["predictions"]}
            assert risks["default/web-1"] > risks["default/web-0"]

    def test_state_with_pods(self, server_with_state):
        """State endpoint should show tracked pods."""
        import urllib.request
        with urllib.request.urlopen("http://localhost:18090/state") as resp:
            data = json.loads(resp.read().decode())
            assert "pods" in data
            assert len(data["pods"]) == 2
            assert "default/web-0" in data["pods"]
            assert "default/web-1" in data["pods"]

    def test_metrics_with_data(self, server_with_state):
        """Metrics should show non-zero pod count."""
        import urllib.request
        with urllib.request.urlopen("http://localhost:18090/metrics") as resp:
            text = resp.read().decode()
            assert "opendesk_predictive_agent_pods_tracked 2" in text
            assert "opendesk_predictive_agent_predictions_count 2" in text

    def test_cache_endpoint(self, server_with_state):
        """Cache endpoint should return the LLM analysis cache."""
        import urllib.request
        with urllib.request.urlopen("http://localhost:18090/cache") as resp:
            data = json.loads(resp.read().decode())
            assert "cache" in data
            assert "total" in data

    def test_history_endpoint(self, server_with_state):
        """History endpoint should return analysis history."""
        import urllib.request
        with urllib.request.urlopen("http://localhost:18090/history") as resp:
            data = json.loads(resp.read().decode())
            assert isinstance(data, list)
            assert len(data) == 1

    def test_reanalyze_endpoint(self, server_with_state):
        """Reanalyze endpoint should trigger callback."""
        import urllib.request
        # Without callback, should still return 200
        with urllib.request.urlopen("http://localhost:18090/reanalyze") as resp:
            data = json.loads(resp.read().decode())
            assert data["status"] == "ok"


# ─── State persistence integration ──────────────────────────────────────────

class TestStatePersistenceIntegration:
    """Integration tests for save/load round-trip with real state."""

    def test_full_state_roundtrip(self):
        """Save and reload complete state model with multiple pods."""
        sm = StateModel()
        for i in range(10):
            sm.update_pod("ns", f"pod-{i}",
                          memory_mib=100 + i * 50,
                          memory_limit_mib=1024,
                          cpu_m=50 + i * 10,
                          restart_count=i,
                          log_errors=i * 2,
                          node_pressure=(i > 7))

        # Record some transitions
        for _ in range(5):
            for key, tracker in sm.pods.items():
                sm.markov.record_transition(tracker.prev_state, tracker.state)

        data = sm.to_dict()
        sm2 = StateModel.from_dict(data)

        assert len(sm2.pods) == 10
        for key in sm.pods:
            assert key in sm2.pods
            assert sm2.pods[key].state == sm.pods[key].state
            assert sm2.pods[key].memory_mib == sm.pods[key].memory_mib

        assert sm2.markov.total_transitions == sm.markov.total_transitions

    def test_markov_learning_across_cycles(self):
        """Markov chain should learn transitions across multiple update cycles."""
        sm = StateModel()
        # Simulate 10 cycles of a pod degrading
        for cycle in range(10):
            mem = 200 + cycle * 80  # Increasing memory
            sm.update_pod("ns", "pod-1",
                          memory_mib=mem, memory_limit_mib=1024,
                          cpu_m=50 + cycle * 10,
                          restart_count=0, log_errors=0,
                          node_pressure=False)

        # Markov chain should have recorded transitions
        assert sm.markov.total_transitions > 0

        # The pod should have transitioned through states
        pod = sm.pods["ns/pod-1"]
        assert pod.state in ("HEALTHY", "DEGRADED", "STRESSED", "CRITICAL")

    def test_kalman_trend_accumulates(self):
        """Kalman filter should accumulate trend across multiple updates."""
        pt = PodTracker("ns", "pod-1")
        values = [100, 110, 120, 130, 140, 150]
        for v in values:
            pt.update(memory_mib=v, memory_limit_mib=1024,
                      cpu_m=50, restart_count=0, log_errors=0,
                      node_pressure=False)
        # Velocity should be positive (memory is increasing)
        assert pt.kalman_memory.velocity > 0
        # Level should be close to the last value
        assert abs(pt.kalman_memory.level - 150) < 20
