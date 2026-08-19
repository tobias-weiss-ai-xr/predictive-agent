"""Pod state tracking with Kalman filters and state classification."""

from predictive_agent.kalman import KalmanTrend
from predictive_agent.markov import MarkovChain


def classify_state(memory_pct, cpu_pct, restart_rate, log_errors, node_pressure, markov_state):
    """Classify pod state based on current metrics.

    Args:
        memory_pct: Memory usage percentage
        cpu_pct: CPU usage percentage
        restart_rate: Pod restarts per hour
        log_errors: Log error count
        node_pressure: Whether node is under memory/disk pressure
        markov_state: Current Markov chain state

    Returns:
        str: One of HEALTHY, DEGRADED, STRESSED, CRITICAL
    """
    score = 0

    # Memory scoring
    if memory_pct > 95:
        score += 4
    elif memory_pct > 85:
        score += 3
    elif memory_pct > 70:
        score += 2
    elif memory_pct > 50:
        score += 1

    # CPU scoring
    if cpu_pct > 95:
        score += 4
    elif cpu_pct > 80:
        score += 3
    elif cpu_pct > 60:
        score += 2
    elif cpu_pct > 40:
        score += 1

    # Restart scoring
    if restart_rate > 5:
        score += 4
    elif restart_rate > 3:
        score += 3
    elif restart_rate > 1:
        score += 2
    elif restart_rate > 0:
        score += 1

    # Log errors scoring
    if log_errors > 10:
        score += 4
    elif log_errors > 5:
        score += 3
    elif log_errors > 2:
        score += 2
    elif log_errors > 0:
        score += 1

    # Node pressure scoring
    if node_pressure:
        score += 3

    # Markov state influence
    markov_scores = {
        "HEALTHY": 0,
        "DEGRADED": 1,
        "STRESSED": 2,
        "CRITICAL": 3,
    }
    score += markov_scores.get(markov_state, 0)

    # Determine state
    if score >= 10:
        return "CRITICAL"
    elif score >= 6:
        return "STRESSED"
    elif score >= 3:
        return "DEGRADED"
    else:
        return "HEALTHY"


class PodTracker:
    """Track state of a single pod using Kalman filters."""

    def __init__(self, namespace, name):
        self.namespace = namespace
        self.name = name
        self.pod_key = f"{namespace}/{name}"
        self.state = "HEALTHY"
        self.prev_state = "HEALTHY"
        self.kalman_memory = KalmanTrend()
        self.kalman_cpu = KalmanTrend()
        self.memory_pct = 0.0
        self.cpu_pct = 0.0
        self.memory_mib = 0
        self.memory_limit_mib = 0
        self.cpu_m = 0
        self.restart_count = 0
        self.log_errors = 0
        self.node_pressure = False
        self._timestamps = []

    def update(self, memory_mib, memory_limit_mib, cpu_m, restart_count, log_errors, node_pressure):
        """Update pod metrics and Kalman filters."""
        self.memory_mib = memory_mib
        self.memory_limit_mib = memory_limit_mib
        self.cpu_m = cpu_m
        self.restart_count = restart_count
        self.log_errors = log_errors
        self.node_pressure = node_pressure

        # Update Kalman filters
        self.kalman_memory.update(memory_mib)
        self.kalman_cpu.update(cpu_m)

        # Calculate percentages
        if memory_limit_mib > 0:
            self.memory_pct = (memory_mib / memory_limit_mib) * 100
        else:
            self.memory_pct = 0.0

        # Estimate CPU percentage (assume 1000m = 100%)
        self.cpu_pct = (cpu_m / 1000) * 100

        # Classify state
        self.prev_state = self.state
        self.state = classify_state(
            memory_pct=self.memory_pct,
            cpu_pct=self.cpu_pct,
            restart_rate=restart_count,  # Simplified: use count as rate proxy
            log_errors=log_errors,
            node_pressure=node_pressure,
            markov_state=self.state
        )

        self._timestamps.append(len(self._timestamps))

    @property
    def memory_trend(self):
        """Current memory trend in MiB/min."""
        return self.kalman_memory.velocity

    @property
    def cpu_trend(self):
        """Current CPU trend in millicores/min."""
        return self.kalman_cpu.velocity

    def time_to_failure(self, threshold_pct=95):
        """Estimate time to reach memory threshold.

        Args:
            threshold_pct: Memory percentage threshold for failure

        Returns:
            float or None: Estimated minutes to failure, or None if not approaching threshold
        """
        if self.memory_limit_mib <= 0:
            return None

        threshold_mib = self.memory_limit_mib * (threshold_pct / 100)
        remaining = threshold_mib - self.memory_mib

        if remaining <= 0:
            return 0.0

        velocity = self.kalman_memory.velocity
        if velocity <= 0:
            return None

        return remaining / velocity


class StateModel:
    """Global state model for all tracked pods."""

    def __init__(self):
        self.pods = {}
        self.markov = MarkovChain()

    def track_pod(self, namespace, name):
        """Start tracking a pod."""
        pod_key = f"{namespace}/{name}"
        if pod_key not in self.pods:
            self.pods[pod_key] = PodTracker(namespace, name)
        return self.pods[pod_key]

    def update_pod(self, namespace, name, memory_mib, memory_limit_mib, cpu_m,
                   restart_count, log_errors, node_pressure):
        """Update pod metrics and return the tracker.

        Records Markov chain state transitions when the pod's state changes.
        """
        tracker = self.track_pod(namespace, name)
        prev_state = tracker.state
        tracker.update(
            memory_mib=memory_mib,
            memory_limit_mib=memory_limit_mib,
            cpu_m=cpu_m,
            restart_count=restart_count,
            log_errors=log_errors,
            node_pressure=node_pressure
        )
        # Record Markov transition when state changes
        if tracker.state != prev_state:
            self.markov.record_transition(prev_state, tracker.state)
        return tracker

    def to_dict(self):
        """Serialize state model to dict."""
        return {
            "pods": {key: {
                "namespace": pod.namespace,
                "name": pod.name,
                "state": pod.state,
                "kalman_memory": pod.kalman_memory.__dict__,
                "kalman_cpu": pod.kalman_cpu.__dict__,
                "memory_pct": pod.memory_pct,
                "cpu_pct": pod.cpu_pct,
                "memory_mib": pod.memory_mib,
                "memory_limit_mib": pod.memory_limit_mib,
                "cpu_m": pod.cpu_m,
                "restart_count": pod.restart_count,
                "log_errors": pod.log_errors,
                "node_pressure": pod.node_pressure,
            } for key, pod in self.pods.items()},
            "markov": self.markov.to_dict() if self.markov else None,
        }

    @classmethod
    def from_dict(cls, data):
        """Deserialize state model from dict."""
        model = cls()
        if "pods" in data:
            for key, pod_data in data["pods"].items():
                tracker = PodTracker(pod_data["namespace"], pod_data["name"])
                tracker.state = pod_data["state"]
                tracker.memory_pct = pod_data["memory_pct"]
                tracker.cpu_pct = pod_data["cpu_pct"]
                tracker.memory_mib = pod_data["memory_mib"]
                tracker.memory_limit_mib = pod_data["memory_limit_mib"]
                tracker.cpu_m = pod_data["cpu_m"]
                tracker.restart_count = pod_data["restart_count"]
                tracker.log_errors = pod_data["log_errors"]
                tracker.node_pressure = pod_data["node_pressure"]

                # Restore Kalman filters
                tracker.kalman_memory.__dict__ = pod_data["kalman_memory"]
                tracker.kalman_cpu.__dict__ = pod_data["kalman_cpu"]

                model.pods[key] = tracker

        if "markov" in data and data["markov"]:
            from predictive_agent.markov import MarkovChain
            model.markov = MarkovChain.from_dict(data["markov"])

        return model