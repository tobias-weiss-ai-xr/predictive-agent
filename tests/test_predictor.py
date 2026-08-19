"""Test prediction engine."""
import pytest
from dev_agent.predictor import Predictor, PredictionResult


def test_prediction_result_creation():
    """Test PredictionResult dataclass."""
    pr = PredictionResult(
        pod_key="opendesk/openldap-0",
        risk_score=0.75,
        ttf_minutes=15,
        confidence=0.85,
        markov_state="STRESSED",
        memory_trend=2.5,
        cpu_trend=10.0,
        memory_pct=85.0,
        cpu_pct=60.0,
    )
    assert pr.pod_key == "opendesk/openldap-0"
    assert pr.risk_score == 0.75
    assert pr.ttf_minutes == 15
    assert pr.confidence == 0.85


def test_predictor_creation():
    """Test Predictor creation."""
    p = Predictor(risk_threshold=0.5)
    assert p.risk_threshold == 0.5


def test_predictor_predict_low_risk():
    """Test prediction for low-risk pod."""
    p = Predictor(risk_threshold=0.5)
    result = p.predict(
        pod_key="ns/pod-0",
        memory_pct=40.0,
        memory_trend_mib_per_min=0.1,
        memory_limit_mib=1024,
        memory_mib=400,
        cpu_pct=20.0,
        restart_rate_per_hr=0.0,
        log_error_rate_per_min=0.0,
        node_memory_pressure=False,
        node_disk_pressure=False,
        markov_state="HEALTHY",
        markov_p_critical=0.01,
        markov_p_failed=0.001,
    )
    assert result.risk_score < 0.5
    assert result.markov_state == "HEALTHY"


def test_predictor_predict_high_risk():
    """Test prediction for high-risk pod."""
    p = Predictor(risk_threshold=0.5)
    result = p.predict(
        pod_key="ns/pod-0",
        memory_pct=95.0,
        memory_trend_mib_per_min=5.0,
        memory_limit_mib=1024,
        memory_mib=970,
        cpu_pct=90.0,
        restart_rate_per_hr=5.0,
        log_error_rate_per_min=10.0,
        node_memory_pressure=True,
        node_disk_pressure=False,
        markov_state="CRITICAL",
        markov_p_critical=0.5,
        markov_p_failed=0.2,
    )
    assert result.risk_score > 0.5
    assert result.ttf_minutes is not None
    assert result.ttf_minutes > 0


def test_predictor_predict_no_ttf():
    """Test prediction when no TTF (stable memory)."""
    p = Predictor(risk_threshold=0.5)
    result = p.predict(
        pod_key="ns/pod-0",
        memory_pct=50.0,
        memory_trend_mib_per_min=0.0,
        memory_limit_mib=1024,
        memory_mib=512,
        cpu_pct=30.0,
        restart_rate_per_hr=0.0,
        log_error_rate_per_min=0.0,
        node_memory_pressure=False,
        node_disk_pressure=False,
        markov_state="HEALTHY",
        markov_p_critical=0.0,
        markov_p_failed=0.0,
    )
    assert result.ttf_minutes is None or result.ttf_minutes == 0


def test_predictor_at_risk():
    """Test getting pods above risk threshold."""
    p = Predictor(risk_threshold=0.3)
    # Add some predictions
    p.add_prediction("ns/pod-1", PredictionResult(
        "ns/pod-1", 0.8, 10, 0.9, "CRITICAL", 3.0, 50.0, 90.0, 80.0
    ))
    p.add_prediction("ns/pod-2", PredictionResult(
        "ns/pod-2", 0.1, None, 0.5, "HEALTHY", 0.0, 10.0, 40.0, 20.0
    ))
    at_risk = p.get_at_risk()
    assert len(at_risk) == 1
    assert at_risk[0].pod_key == "ns/pod-1"
