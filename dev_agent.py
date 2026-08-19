"""openDesk Dev Agent v4.0 — Predictive Health Monitor"""
import math
import time


class MarkovChain:
    """First-order Markov chain for pod state transitions."""
    
    STATES = ["HEALTHY", "DEGRADED", "STRESSED", "CRITICAL", "FAILED", "RECOVERED"]
    STATE_IDX = {s: i for i, s in enumerate(STATES)}
    
    # Prior counts (pseudo-observations)
    PRIOR_COUNTS = [
        [95, 4, 1, 0, 0, 0],
        [30, 50, 15, 4, 0, 1],
        [10, 20, 40, 25, 4, 1],
        [5, 5, 10, 60, 15, 5],
        [0, 0, 0, 0, 80, 20],
        [80, 15, 5, 0, 0, 0],
    ]
    
    def __init__(self):
        self.counts = [row[:] for row in self.PRIOR_COUNTS]
        self.total_transitions = 0
        self.last_updated = 0
    
    def record_transition(self, from_state, to_state):
        """Record a state transition."""
        from_idx = self.STATE_IDX.get(from_state, 0)
        to_idx = self.STATE_IDX.get(to_state, 0)
        self.counts[from_idx][to_idx] += 1
        self.total_transitions += 1
        self.last_updated = time.time()
    
    def transition_matrix(self):
        """Return normalized transition probabilities."""
        matrix = []
        for row in self.counts:
            total = sum(row)
            if total > 0:
                matrix.append([v / total for v in row])
            else:
                matrix.append([1/6] * 6)
        return matrix
    
    def predict(self, from_state, steps=1):
        """Predict state distribution after n steps."""
        matrix = self.transition_matrix()
        from_idx = self.STATE_IDX.get(from_state, 0)
        
        # Start with one-hot vector
        vec = [0.0] * 6
        vec[from_idx] = 1.0
        
        # Matrix power
        for _ in range(steps):
            new_vec = [0.0] * 6
            for i in range(6):
                for j in range(6):
                    new_vec[j] += vec[i] * matrix[i][j]
            vec = new_vec
        
        return {self.STATES[i]: vec[i] for i in range(6)}
    
    def to_dict(self):
        """Serialize to dict for persistence."""
        return {
            "counts": self.counts,
            "total_transitions": self.total_transitions,
            "last_updated": self.last_updated,
        }
    
    @classmethod
    def from_dict(cls, data):
        """Deserialize from dict."""
        chain = cls()
        if data:
            chain.counts = data.get("counts", [row[:] for row in cls.PRIOR_COUNTS])
            chain.total_transitions = data.get("total_transitions", 0)
            chain.last_updated = data.get("last_updated", 0)
        return chain


def parse_cpu(value):
    """Parse CPU string to millicores."""
    value = value.strip()
    if value.endswith('m'):
        return int(value[:-1])
    try:
        return int(float(value) * 1000)
    except ValueError:
        return 0


def parse_memory(value):
    """Parse memory string to MiB."""
    value = value.strip()
    if value.endswith('Mi'):
        return int(value[:-2])
    elif value.endswith('Gi'):
        return int(value[:-2]) * 1024
    elif value.endswith('Ki'):
        return int(value[:-2]) // 1024
    elif value.endswith('Ti'):
        return int(value[:-2]) * 1024 * 1024
    try:
        return int(value) // (1024 * 1024)  # bytes to MiB
    except ValueError:
        return 0


def collect_top_metrics(output):
    """Parse kubectl top pods -A output."""
    metrics = {}
    lines = output.strip().split('\n')
    for line in lines[1:]:  # skip header
        parts = line.split()
        if len(parts) < 4:
            continue
        ns = parts[0]
        name = parts[1]
        cpu = parse_cpu(parts[2])
        mem = parse_memory(parts[3])
        metrics[f"{ns}/{name}"] = {"cpu_m": cpu, "memory_mib": mem}
    return metrics


def calculate_risk(pod_metrics, markov_state, markov_p_critical, markov_p_failed):
    """Calculate Bayesian risk score (0.0 to 1.0)."""
    prior = 0.01  # base rate
    lr = 1.0  # likelihood ratio multiplier
    
    mem_pct = pod_metrics.get("memory_pct", 0)
    mem_trend = pod_metrics.get("memory_trend_mib_per_min", 0)
    cpu_pct = pod_metrics.get("cpu_pct", 0)
    restart_rate = pod_metrics.get("restart_rate_per_hr", 0)
    log_error_rate = pod_metrics.get("log_error_rate_per_min", 0)
    node_mem_pressure = pod_metrics.get("node_memory_pressure", False)
    node_disk_pressure = pod_metrics.get("node_disk_pressure", False)
    
    # Memory
    if mem_pct > 95:
        lr *= 10.0
    elif mem_pct > 85:
        lr *= 5.0
    elif mem_pct > 70:
        lr *= 2.0
    
    # Memory trend + time to OOM
    if mem_trend > 0 and mem_pct > 70:
        if pod_metrics.get("memory_limit_mib", 0) > 0:
            remaining = pod_metrics["memory_limit_mib"] - pod_metrics["memory_mib"]
            if mem_trend > 0:
                ttf = remaining / mem_trend  # minutes
                if ttf < 5:
                    lr *= 8.0
                elif ttf < 10:
                    lr *= 4.0
                elif ttf < 30:
                    lr *= 2.0
    
    # CPU
    if cpu_pct > 95:
        lr *= 3.0
    elif cpu_pct > 80:
        lr *= 1.5
    
    # Restart rate
    if restart_rate > 5:
        lr *= 10.0
    elif restart_rate > 3:
        lr *= 4.0
    elif restart_rate > 1:
        lr *= 2.0
    
    # Log errors
    if log_error_rate > 10:
        lr *= 3.0
    elif log_error_rate > 5:
        lr *= 2.0
    
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
    lr *= (1 + markov_p_critical * 5 + markov_p_failed * 10)
    
    # Bayesian update
    posterior = (prior * lr) / (prior * lr + (1 - prior))
    return min(posterior, 0.99)


class KalmanTrend:
    """2D Kalman filter for trend estimation."""
    
    def __init__(self, process_noise=1.0, measurement_noise=100.0, dt=1.0):
        self.x = [0.0, 0.0]  # [level, velocity]
        self.P = [[1000.0, 0.0], [0.0, 1000.0]]  # Covariance
        self.F = [[1.0, dt], [0.0, 1.0]]  # State transition
        self.H = [1.0, 0.0]  # Observation matrix
        self.Q = [[process_noise * dt, 0.0], [0.0, process_noise * dt]]  # Process noise
        self.R = measurement_noise  # Measurement noise
        self.initialized = False
        self.last_update = 0.0
    
    def update(self, measurement, timestamp=None):
        """Update Kalman filter with new measurement."""
        if not self.initialized:
            self.x = [measurement, 0.0]
            self.P = [[self.R, 0.0], [0.0, self.R]]
            self.initialized = True
            self.last_update = timestamp or time.time()
            return
        
        # Predict
        x_pred = [
            self.F[0][0] * self.x[0] + self.F[0][1] * self.x[1],
            self.F[1][0] * self.x[0] + self.F[1][1] * self.x[1]
        ]
        
        # P_pred = F @ P @ F.T + Q
        FP00 = self.F[0][0] * self.P[0][0] + self.F[0][1] * self.P[1][0]
        FP01 = self.F[0][0] * self.P[0][1] + self.F[0][1] * self.P[1][1]
        FP10 = self.F[1][0] * self.P[0][0] + self.F[1][1] * self.P[1][0]
        FP11 = self.F[1][0] * self.P[0][1] + self.F[1][1] * self.P[1][1]
        
        P00 = FP00 * self.F[0][0] + FP01 * self.F[0][1] + self.Q[0][0]
        P01 = FP00 * self.F[1][0] + FP01 * self.F[1][1] + self.Q[0][1]
        P10 = FP10 * self.F[0][0] + FP11 * self.F[0][1] + self.Q[1][0]
        P11 = FP10 * self.F[1][0] + FP11 * self.F[1][1] + self.Q[1][1]
        
        # Innovation
        y = measurement - (self.H[0] * x_pred[0] + self.H[1] * x_pred[1])
        
        # Innovation covariance
        S = P00 * self.H[0] * self.H[0] + P01 * self.H[0] * self.H[1] + \
            P10 * self.H[1] * self.H[0] + P11 * self.H[1] * self.H[1] + self.R
        
        # Kalman gain
        K0 = (P00 * self.H[0] + P01 * self.H[1]) / S
        K1 = (P10 * self.H[0] + P11 * self.H[1]) / S
        
        # Update state
        self.x = [
            x_pred[0] + K0 * y,
            x_pred[1] + K1 * y
        ]
        
        # Update covariance
        self.P = [
            [
                P00 - K0 * self.H[0] * P00 - K0 * self.H[1] * P10,
                P01 - K0 * self.H[0] * P01 - K0 * self.H[1] * P11
            ],
            [
                P10 - K1 * self.H[0] * P00 - K1 * self.H[1] * P10,
                P11 - K1 * self.H[0] * P01 - K1 * self.H[1] * P11
            ]
        ]
        
        self.last_update = timestamp or time.time()
    
    @property
    def level(self):
        """Current level estimate."""
        return self.x[0]
    
    @property
    def velocity(self):
        """Current velocity estimate."""
        return self.x[1]
    
    @property
    def level_uncertainty(self):
        """Level uncertainty (standard deviation)."""
        return self.P[0][0] ** 0.5
    
    @property
    def velocity_uncertainty(self):
        """Velocity uncertainty (standard deviation)."""
        return self.P[1][1] ** 0.5
    
    def predict(self, steps=1.0):
        """Predict state at future time."""
        pred_level = self.x[0] + self.x[1] * steps
        pred_var = self.P[0][0] + steps * steps * self.P[1][1] + 2 * steps * self.P[0][1]
        pred_sigma = pred_var ** 0.5 if pred_var > 0 else 0.0
        return pred_level, pred_sigma
    
    def time_to_threshold(self, threshold, max_steps=180):
        """Estimate time to reach threshold."""
        if self.velocity <= 0:
            return max_steps, 0.0
        
        steps = (threshold - self.x[0]) / self.velocity
        if steps <= 0:
            return 0, 1.0
        if steps > max_steps:
            return max_steps, 0.0
        
        pred_level, pred_sigma = self.predict(steps)
        z = (pred_level - threshold) / (pred_sigma + 1e-9)
        confidence = 0.5 * (1 + math.erf(z / math.sqrt(2)))
        return int(steps), confidence