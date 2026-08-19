"""Test Bayesian risk scoring."""
import pytest
from predictive_agent.risk import calculate_risk


def test_risk_calculation_low_risk():
    """Test low risk calculation."""
    pod_metrics = {
        "memory_pct": 50.0,
        "memory_trend_mib_per_min": 0.5,
        "cpu_pct": 30.0,
        "restart_rate_per_hr": 0.0,
        "log_error_rate_per_min": 0.0,
        "node_memory_pressure": False,
        "node_disk_pressure": False,
    }

    risk = calculate_risk(pod_metrics, "HEALTHY", 0.01, 0.001)
    assert 0 <= risk <= 0.1  # Low risk


def test_risk_calculation_high_risk():
    """Test high risk calculation."""
    pod_metrics = {
        "memory_pct": 95.0,
        "memory_trend_mib_per_min": 5.0,
        "cpu_pct": 90.0,
        "restart_rate_per_hr": 5.0,
        "log_error_rate_per_min": 10.0,
        "node_memory_pressure": True,
        "node_disk_pressure": False,
    }

    risk = calculate_risk(pod_metrics, "CRITICAL", 0.5, 0.2)
    assert risk > 0.8  # High risk


def test_risk_calculation_with_memory_ttf():
    """Test risk calculation with memory time-to-failure."""
    pod_metrics = {
        "memory_pct": 85.0,
        "memory_trend_mib_per_min": 2.0,
        "memory_limit_mib": 1024,
        "memory_mib": 870,
        "cpu_pct": 40.0,
        "restart_rate_per_hr": 1.0,
        "log_error_rate_per_min": 2.0,
        "node_memory_pressure": False,
        "node_disk_pressure": False,
    }

    risk = calculate_risk(pod_metrics, "STRESSED", 0.1, 0.05)
    assert risk > 0.1  # Medium risk due to TTF


def test_risk_calculation_no_metrics():
    """Test risk with empty metrics."""
    risk = calculate_risk({}, "HEALTHY", 0.0, 0.0)
    assert 0 <= risk <= 0.1  # Should be near prior


def test_risk_calculation_boundary():
    """Test risk is bounded 0-1."""
    pod_metrics = {
        "memory_pct": 100.0,
        "memory_trend_mib_per_min": 100.0,
        "cpu_pct": 100.0,
        "restart_rate_per_hr": 100.0,
        "log_error_rate_per_min": 100.0,
        "node_memory_pressure": True,
        "node_disk_pressure": True,
    }
    risk = calculate_risk(pod_metrics, "FAILED", 1.0, 1.0)
    assert risk <= 0.99
    assert risk >= 0.0


def test_risk_calculation_markov_impact():
    """Test that Markov state affects risk."""
    base_metrics = {
        "memory_pct": 80.0,
        "cpu_pct": 50.0,
        "restart_rate_per_hr": 1.0,
        "log_error_rate_per_min": 1.0,
    }
    risk_healthy = calculate_risk(base_metrics, "HEALTHY", 0.0, 0.0)
    risk_critical = calculate_risk(base_metrics, "CRITICAL", 0.3, 0.1)
    assert risk_critical > risk_healthy
