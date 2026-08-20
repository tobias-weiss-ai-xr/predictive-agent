"""Bayesian risk scoring combining multiple signals."""

import math


def calculate_risk(pod_metrics, markov_state, markov_p_critical, markov_p_failed):
    """Calculate Bayesian risk score (0.0 to 1.0).

    Combines:
    - Memory percentage
    - Memory trend (via time-to-failure)
    - CPU percentage
    - CPU trend (millicores/min)
    - Restart rate
    - Log error rate
    - Node pressure (memory/disk)
    - Markov state + transition probabilities
    - Anomaly scores (memory and CPU, from Kalman innovation)
    - Blast radius (from knowledge graph, 0=not connected)

    Args:
        pod_metrics: dict with keys like memory_pct, memory_trend_mib_per_min,
                     cpu_pct, cpu_trend_m_per_min, restart_rate_per_hr,
                     log_error_rate_per_min, node_memory_pressure,
                     node_disk_pressure, memory_limit_mib, memory_mib,
                     memory_anomaly_score, cpu_anomaly_score, blast_radius
        markov_state: current Markov state string
        markov_p_critical: probability of transitioning to CRITICAL
        markov_p_failed: probability of transitioning to FAILED

    Returns:
        float: risk score 0.0 to 0.99
    """
    prior = 0.02  # base rate (~2% of pods experience issues per cycle)
    lr = 1.0  # likelihood ratio multiplier

    mem_pct = pod_metrics.get("memory_pct", 0)
    mem_trend = pod_metrics.get("memory_trend_mib_per_min", 0)
    cpu_pct = pod_metrics.get("cpu_pct", 0)
    cpu_trend = pod_metrics.get("cpu_trend_m_per_min", 0)
    restart_rate = pod_metrics.get("restart_rate_per_hr", 0)
    log_error_rate = pod_metrics.get("log_error_rate_per_min", 0)
    node_mem_pressure = pod_metrics.get("node_memory_pressure", False)
    node_disk_pressure = pod_metrics.get("node_disk_pressure", False)
    mem_anomaly = pod_metrics.get("memory_anomaly_score", 0)
    cpu_anomaly = pod_metrics.get("cpu_anomaly_score", 0)
    blast_radius = pod_metrics.get("blast_radius", 0)

    # Memory percentage
    if mem_pct > 95:
        lr *= 12.0
    elif mem_pct > 85:
        lr *= 6.0
    elif mem_pct > 70:
        lr *= 2.5

    # Memory trend + time to OOM
    if mem_trend > 0 and mem_pct > 70:
        if pod_metrics.get("memory_limit_mib", 0) > 0:
            remaining = pod_metrics["memory_limit_mib"] - pod_metrics.get("memory_mib", 0)
            if mem_trend > 0:
                ttf = remaining / mem_trend  # minutes
                if ttf < 5:
                    lr *= 8.0
                elif ttf < 10:
                    lr *= 4.0
                elif ttf < 30:
                    lr *= 2.0

    # CPU percentage
    if cpu_pct > 95:
        lr *= 3.0
    elif cpu_pct > 80:
        lr *= 1.5

    # CPU trend (rapid CPU growth is a warning sign)
    if cpu_trend > 50:  # >50 millicores/min sustained growth
        lr *= 2.0
    elif cpu_trend > 20:
        lr *= 1.3

    # Restart rate (any restart is a strong signal — backtester shows
    # restart_count > 0 correlates with actual failures)
    if restart_rate > 5:
        lr *= 15.0
    elif restart_rate > 3:
        lr *= 10.0
    elif restart_rate > 1:
        lr *= 6.0
    elif restart_rate > 0:
        lr *= 3.0

    # Log errors
    if log_error_rate > 10:
        lr *= 4.0
    elif log_error_rate > 5:
        lr *= 2.5
    elif log_error_rate > 0:
        lr *= 1.5

    # Node pressure
    if node_mem_pressure:
        lr *= 4.0
    if node_disk_pressure:
        lr *= 6.0

    # Markov state
    if markov_state == "CRITICAL":
        lr *= 20.0
    elif markov_state == "STRESSED":
        lr *= 3.0
    elif markov_state == "DEGRADED":
        lr *= 1.5

    # Markov prediction
    lr *= 1 + markov_p_critical * 5 + markov_p_failed * 10

    # Anomaly detection (3-sigma spikes from Kalman filter)
    if mem_anomaly > 3:
        lr *= 3.0
    elif mem_anomaly > 2:
        lr *= 1.5
    if cpu_anomaly > 3:
        lr *= 2.0
    elif cpu_anomaly > 2:
        lr *= 1.3

    # Blast radius (knowledge graph impact)
    # High blast radius means many pods depend on this one — escalate risk
    if blast_radius > 20:
        lr *= 3.0
    elif blast_radius > 10:
        lr *= 2.0
    elif blast_radius > 5:
        lr *= 1.5

    # Bayesian update
    posterior = (prior * lr) / (prior * lr + (1 - prior))
    return min(posterior, 0.99)
