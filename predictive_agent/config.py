"""Configuration for predictive-agent v4.0."""

import os

# ─── Core ────────────────────────────────────────────────────────────────────
OPERATOR_NAME = os.environ.get("OPERATOR_NAME", "opendesk-predictive-agent")
OPERATOR_NAMESPACE = os.environ.get("OPERATOR_NAMESPACE", "opendesk-predictive-agent")
OPERATOR_VERSION = "4.0.0"
WATCH_NAMESPACES = os.environ.get(
    "OPERATOR_WATCH_NAMESPACES", "opendesk,opendesk-edu,default,llm"
).split(",")
SKIP_NAMESPACES = {"opendesk-predictive-agent"}

# ─── LLM Backend ─────────────────────────────────────────────────────────────
LLM_BACKEND = os.environ.get("LLM_BACKEND", "ollama")  # ollama, saia, openai
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://ollama.llm.svc.cluster.local:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen3-30b-a3b:latest")
OLLAMA_TIMEOUT = int(os.environ.get("OLLAMA_TIMEOUT", "180"))

SAIA_API_URL = os.environ.get("SAIA_API_URL", "https://api.saia.ai/v1")
SAIA_API_KEY = os.environ.get("SAIA_API_KEY", "")
SAIA_MODEL = os.environ.get("SAIA_MODEL", "qwen3.5-35b-a3b")

OPENAI_API_URL = os.environ.get("OPENAI_API_URL", "https://api.openai.com/v1")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o")

TUD_API_URL = os.environ.get("TUD_API_URL", "https://api.tud-llm.de/v1")
TUD_API_KEY = os.environ.get("TUD_API_KEY", "")
TUD_MODEL = os.environ.get("TUD_MODEL", "GLM-5.2-AWQ-INT4")

# ─── Reconcile ───────────────────────────────────────────────────────────────
RECONCILE_INTERVAL = int(os.environ.get("RECONCILE_INTERVAL", "60"))
ANALYSIS_TTL = int(os.environ.get("ANALYSIS_TTL", "300"))
ANALYSIS_TTL_MAX = int(os.environ.get("ANALYSIS_TTL_MAX", "1200"))
MAX_PODS_PER_CYCLE = int(os.environ.get("MAX_PODS_PER_CYCLE", "3"))
LOG_VERBOSITY = os.environ.get("LOG_VERBOSITY", "info")

# ─── Prediction ──────────────────────────────────────────────────────────────
PREDICTION_ENABLED = os.environ.get("PREDICTION_ENABLED", "true").lower() == "true"
PREDICTION_RISK_THRESHOLD = float(os.environ.get("PREDICTION_RISK_THRESHOLD", "0.5"))

# ─── Kalman Filter ───────────────────────────────────────────────────────────
KALMAN_PROCESS_NOISE = float(os.environ.get("KALMAN_PROCESS_NOISE", "1.0"))
KALMAN_MEASUREMENT_NOISE = float(os.environ.get("KALMAN_MEASUREMENT_NOISE", "100.0"))
# Per-metric Kalman parameters (override defaults for finer control)
KALMAN_MEMORY_PROCESS_NOISE = float(os.environ.get("KALMAN_MEMORY_PROCESS_NOISE", "0.5"))
KALMAN_MEMORY_MEASUREMENT_NOISE = float(os.environ.get("KALMAN_MEMORY_MEASUREMENT_NOISE", "50.0"))
KALMAN_CPU_PROCESS_NOISE = float(os.environ.get("KALMAN_CPU_PROCESS_NOISE", "5.0"))
KALMAN_CPU_MEASUREMENT_NOISE = float(os.environ.get("KALMAN_CPU_MEASUREMENT_NOISE", "200.0"))

# ─── Persistence ─────────────────────────────────────────────────────────────
STATE_MODEL_FILE = os.environ.get("STATE_MODEL_FILE", "/var/lib/opendesk/state-model.json")
PREDICTIONS_FILE = os.environ.get("PREDICTIONS_FILE", "/var/lib/opendesk/predictions.json")
HISTORY_FILE = os.environ.get("HISTORY_FILE", "/var/lib/opendesk/analysis-history.json")
HISTORY_MAX = int(os.environ.get("HISTORY_MAX", "100"))

# ─── HTTP ────────────────────────────────────────────────────────────────────
HEALTH_PORT = int(
    os.environ.get("OPERATOR_HEALTH_PROBE_BIND_ADDRESS", "0.0.0.0:8081").split(":")[-1]
)
METRICS_PORT = int(
    os.environ.get("OPERATOR_METRICS_BIND_ADDRESS", "0.0.0.0:8080").split(":")[-1]
)

# ─── Unhealthy pod statuses ──────────────────────────────────────────────────
UNHEALTHY_STATUSES = {
    "CrashLoopBackOff", "Error", "OOMKilled", "ImagePullBackOff", "ErrImagePull",
    "ContainerCreating", "PodInitializing", "CreateContainerError",
    "CreateContainerConfigError", "RunContainerError", "InvalidImageName",
    "RegistryUnavailable", "Evicted", "Pending", "Failed", "Unknown",
}

SKIP_LOGS_STATUSES = {
    "ImagePullBackOff", "ErrImagePull", "ContainerCreating",
    "PodInitializing", "CreateContainerError", "CreateContainerConfigError", "Pending",
}

# ─── Markov States ───────────────────────────────────────────────────────────
MARKOV_STATES = ["HEALTHY", "DEGRADED", "STRESSED", "CRITICAL", "FAILED", "RECOVERED"]

# ─── Remediation ────────────────────────────────────────────────────────────
REMEDIATION_ENABLED = os.environ.get("REMEDIATION_ENABLED", "false")
REMEDIATION_DRY_RUN = os.environ.get("REMEDIATION_DRY_RUN", "true")
REMEDIATION_MAX_PER_MIN = os.environ.get("REMEDIATION_MAX_PER_MIN", "5")
REMEDIATION_MAX_PER_HOUR = os.environ.get("REMEDIATION_MAX_PER_HOUR", "50")
REMEDIATION_COOLDOWN_S = os.environ.get("REMEDIATION_COOLDOWN_S", "300")
REMEDIATION_RISK_THRESHOLD = os.environ.get("REMEDIATION_RISK_THRESHOLD", "70.0")
REMEDIATION_PROTECTED_NS = os.environ.get(
    "REMEDIATION_PROTECTED_NS", "kube-system,opendesk-predictive-agent"
)

# ─── Alerting / Notification ────────────────────────────────────────────────
ALERT_EMAIL_TO = os.environ.get("ALERT_EMAIL_TO", "tobias.weiss@uni-marburg.de")
ALERT_EMAIL_FROM = os.environ.get("ALERT_EMAIL_FROM", "predictive-agent@opendesk.scs")
SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.uni-marburg.de")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
SMTP_USE_TLS = os.environ.get("SMTP_USE_TLS", "true").lower() in ("true", "1", "yes")
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "")
WEBHOOK_TIMEOUT = int(os.environ.get("WEBHOOK_TIMEOUT", "10"))
