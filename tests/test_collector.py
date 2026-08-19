"""Test metrics collection from kubectl."""
import pytest
from predictive_agent.collector import parse_cpu, parse_memory, collect_top_metrics, collect_top_nodes, count_log_errors


def test_parse_cpu():
    """Test CPU parsing."""
    assert parse_cpu("100m") == 100
    assert parse_cpu("1.5") == 1500
    assert parse_cpu("0m") == 0
    assert parse_cpu("250m") == 250


def test_parse_cpu_invalid():
    """Test CPU parsing with invalid input."""
    assert parse_cpu("invalid") == 0
    assert parse_cpu("") == 0


def test_parse_memory():
    """Test memory parsing."""
    assert parse_memory("128Mi") == 128
    assert parse_memory("2Gi") == 2048
    assert parse_memory("1024Ki") == 1
    assert parse_memory("1Ti") == 1048576


def test_parse_memory_invalid():
    """Test memory parsing with invalid input."""
    assert parse_memory("invalid") == 0
    assert parse_memory("") == 0


def test_collect_top_metrics():
    """Test collecting metrics from kubectl top pods output."""
    mock_output = """NAMESPACE            NAME                                                  CPU(cores)   MEMORY(bytes)    
argocd               argocd-application-controller-0                       14m          169Mi            
opendesk             openldap-0                                           17m          124Mi            
"""
    metrics = collect_top_metrics(mock_output)
    assert "argocd/argocd-application-controller-0" in metrics
    assert metrics["argocd/argocd-application-controller-0"]["cpu_m"] == 14
    assert metrics["argocd/argocd-application-controller-0"]["memory_mib"] == 169
    assert "opendesk/openldap-0" in metrics


def test_collect_top_metrics_empty():
    """Test collecting metrics from empty output."""
    metrics = collect_top_metrics("")
    assert metrics == {}


def test_collect_top_nodes():
    """Test collecting node metrics."""
    mock_output = """NAME           CPU(cores)   CPU%   MEMORY(bytes)   MEMORY%   
clrz14-06      1000m        10%    8000Mi          20%       
clrz14-07      2000m        20%    16000Mi         40%       
"""
    metrics = collect_top_nodes(mock_output)
    assert "clrz14-06" in metrics
    assert metrics["clrz14-06"]["cpu_m"] == 1000
    assert metrics["clrz14-06"]["memory_mib"] == 8000
    assert metrics["clrz14-06"]["cpu_pct"] == 10.0
    assert metrics["clrz14-07"]["memory_pct"] == 40.0


def test_count_log_errors():
    """Test counting error lines in logs."""
    logs = """[INFO] Starting server
[ERROR] Connection failed
[WARN] Slow query
[FATAL] Database error
[DEBUG] Query executed
Traceback (most recent call last):
panic: runtime error
"""
    count = count_log_errors(logs)
    assert count == 4  # ERROR, FATAL, Traceback, panic


def test_count_log_errors_empty():
    """Test counting errors in empty logs."""
    assert count_log_errors("") == 0
    assert count_log_errors("Just normal logs\nNo errors here") == 0
