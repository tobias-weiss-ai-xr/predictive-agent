"""Prediction engine combining Kalman trends, Markov state, and Bayesian risk."""

import math
from dataclasses import dataclass
from dev_agent.kalman import KalmanTrend
from dev_agent.markov import MarkovChain
from dev_agent.risk import calculate_risk


@dataclass
class PredictionResult:
    """Prediction result for a pod.

    Args:
        pod_key: Pod identifier (namespace/name)
        risk_score: Bayesian risk score (0.0 to 0.99)
        ttf_minutes: Estimated time to failure in minutes, or None if not predictable
        confidence: Confidence in the prediction (0.0 to 1.0)
        markov_state: Current Markov state
        memory_trend: Memory usage trend (MiB/min)
        cpu_trend: CPU usage trend (millicores/min)
        memory_pct: Current memory percentage
        cpu_pct: Current CPU percentage
    """
    pod_key: str
    risk_score: float
    ttf_minutes: float | None
    confidence: float
    markov_state: str
    memory_trend: float
    cpu_trend: float
    memory_pct: float
    cpu_pct: float


class Predictor:
    """Prediction engine combining multiple signals."""

    def __init__(self, risk_threshold=0.5):
        """Initialize predictor.

        Args:
            risk_threshold: Threshold for considering a pod at risk (0.0 to 1.0)
        """
        self.risk_threshold = risk_threshold
        self.predictions = {}
        self.markov = MarkovChain()

    def predict(self, pod_key, memory_pct, memory_trend_mib_per_min, memory_limit_mib,
                memory_mib, cpu_pct, restart_rate_per_hr, log_error_rate_per_min,
                node_memory_pressure, node_disk_pressure, markov_state, markov_p_critical,
                markov_p_failed):
        """Generate prediction for a pod.

        Args:
            pod_key: Pod identifier (namespace/name)
            memory_pct: Current memory percentage
            memory_trend_mib_per_min: Memory trend in MiB/min
            memory_limit_mib: Memory limit in MiB
            memory_mib: Current memory usage in MiB
            cpu_pct: Current CPU percentage
            restart_rate_per_hr: Pod restarts per hour
            log_error_rate_per_min: Log error rate per minute
            node_memory_pressure: Whether node is under memory pressure
            node_disk_pressure: Whether node is under disk pressure
            markov_state: Current Markov state
            markov_p_critical: Probability of transitioning to CRITICAL
            markov_p_failed: Probability of transitioning to FAILED

        Returns:
            PredictionResult with risk score, TTF, and other metrics
        """
        # Calculate time to failure
        ttf_minutes = self._calculate_ttf(
            memory_pct=memory_pct,
            memory_trend_mib_per_min=memory_trend_mib_per_min,
            memory_limit_mib=memory_limit_mib,
            memory_mib=memory_mib
        )

        # Calculate confidence based on TTF predictability
        confidence = self._calculate_confidence(ttf_minutes, memory_trend_mib_per_min)

        # Build metrics dict for risk calculation
        pod_metrics = {
            "memory_pct": memory_pct,
            "memory_trend_mib_per_min": memory_trend_mib_per_min,
            "cpu_pct": cpu_pct,
            "restart_rate_per_hr": restart_rate_per_hr,
            "log_error_rate_per_min": log_error_rate_per_min,
            "node_memory_pressure": node_memory_pressure,
            "node_disk_pressure": node_disk_pressure,
            "memory_limit_mib": memory_limit_mib,
            "memory_mib": memory_mib,
        }

        # Calculate Bayesian risk score
        risk_score = calculate_risk(
            pod_metrics=pod_metrics,
            markov_state=markov_state,
            markov_p_critical=markov_p_critical,
            markov_p_failed=markov_p_failed
        )

        result = PredictionResult(
            pod_key=pod_key,
            risk_score=risk_score,
            ttf_minutes=ttf_minutes,
            confidence=confidence,
            markov_state=markov_state,
            memory_trend=memory_trend_mib_per_min,
            cpu_trend=cpu_pct / 10.0 if cpu_pct > 0 else 0.0,  # Simplified trend estimate
            memory_pct=memory_pct,
            cpu_pct=cpu_pct,
        )

        # Store prediction
        self.predictions[pod_key] = result

        return result

    def _calculate_ttf(self, memory_pct, memory_trend_mib_per_min, memory_limit_mib, memory_mib):
        """Calculate time to failure based on memory trend.

        Returns minutes to failure, or None if not approaching failure.
        """
        if memory_limit_mib <= 0:
            return None

        remaining = memory_limit_mib - memory_mib

        if remaining <= 0:
            return 0.0

        if memory_trend_mib_per_min <= 0:
            return None

        ttf = remaining / memory_trend_mib_per_min

        # Cap at reasonable maximum
        if ttf > 180:
            return None

        return ttf

    def _calculate_confidence(self, ttf_minutes, memory_trend):
        """Calculate prediction confidence.

        Higher confidence for:
        - Clear trend (high absolute velocity)
        - Predictable TTF (not too far in future)
        """
        if ttf_minutes is None:
            return 0.0

        if memory_trend == 0:
            return 0.0

        # Base confidence on TTF predictability
        if ttf_minutes <= 5:
            base_confidence = 0.9
        elif ttf_minutes <= 10:
            base_confidence = 0.85
        elif ttf_minutes <= 30:
            base_confidence = 0.75
        else:
            base_confidence = 0.5

        # Adjust based on trend magnitude
        trend_factor = min(abs(memory_trend) / 10.0, 1.0)
        confidence = base_confidence * (0.5 + 0.5 * trend_factor)

        return min(confidence, 1.0)

    def add_prediction(self, pod_key, result):
        """Add a prediction result manually."""
        self.predictions[pod_key] = result

    def get_at_risk(self):
        """Get all pods above risk threshold.

        Returns:
            list of PredictionResult for pods at risk
        """
        return [
            result for result in self.predictions.values()
            if result.risk_score > self.risk_threshold
        ]