"""Email and webhook notification system for predictive-agent.

Sends alerts via SMTP email and HTTP webhook when remediation actions are taken
or when risk thresholds are exceeded.

Email: uses smtplib with STARTTLS to smtp.uni-marburg.de:587
  - Default recipient: tobias.weiss@uni-marburg.de
  - Configurable via ALERT_EMAIL_TO, SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD

Webhook: uses urllib.request to POST JSON to WEBHOOK_URL
  - Configurable via WEBHOOK_URL
  - No-op if WEBHOOK_URL is empty
"""

from __future__ import annotations

import json
import logging
import os
import smtplib
import ssl
from datetime import datetime, timezone
from email.mime.text import MIMEText
from typing import Optional
from urllib.request import Request, urlopen
from urllib.error import URLError

from predictive_agent.config import (
    ALERT_EMAIL_FROM,
    ALERT_EMAIL_TO,
    SMTP_HOST,
    SMTP_PASSWORD,
    SMTP_PORT,
    SMTP_USE_TLS,
    SMTP_USER,
    WEBHOOK_TIMEOUT,
    WEBHOOK_URL,
)

# Note: these are imported at module load time. create_notifier_from_config()
# reads os.environ directly for testability.

logger = logging.getLogger(__name__)


def _utc_now() -> str:
    """Return current UTC time as ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()


class EmailNotifier:
    """Send email alerts via SMTP with STARTTLS."""

    def __init__(
        self,
        host: str = SMTP_HOST,
        port: int = SMTP_PORT,
        user: str = SMTP_USER,
        password: str = SMTP_PASSWORD,
        from_addr: str = ALERT_EMAIL_FROM,
        to_addrs: Optional[list] = None,
        use_tls: bool = SMTP_USE_TLS,
    ):
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.from_addr = from_addr
        self.to_addrs = to_addrs or [ALERT_EMAIL_TO]
        self.use_tls = use_tls

    def send(self, subject: str, body: str) -> bool:
        """Send an email alert.

        Returns True on success, False on failure (never raises).
        """
        msg = MIMEText(body)
        msg["Subject"] = f"[SCS-Predictive-Agent] {subject}"
        msg["From"] = self.from_addr
        msg["To"] = ", ".join(self.to_addrs)

        try:
            with smtplib.SMTP(self.host, self.port, timeout=10) as server:
                if self.use_tls:
                    server.starttls(context=ssl.create_default_context())
                if self.user and self.password:
                    server.login(self.user, self.password)
                server.sendmail(self.from_addr, self.to_addrs, msg.as_string())
            logger.info("Email alert sent: %s to %s", subject, self.to_addrs)
            return True
        except smtplib.SMTPException as e:
            logger.error("SMTP error sending email: %s", e)
            return False
        except Exception as e:
            logger.error("Error sending email: %s", e)
            return False


class WebhookNotifier:
    """Send JSON webhook notifications via HTTP POST."""

    def __init__(self, url: str = WEBHOOK_URL, timeout: int = WEBHOOK_TIMEOUT):
        self.url = url
        self.timeout = timeout

    def send(self, payload: dict) -> bool:
        """Send a webhook notification.

        Returns True on success (HTTP 200), False on failure.
        If url is empty, returns True (no-op, not an error).
        """
        if not self.url:
            return True

        try:
            data = json.dumps(payload).encode("utf-8")
            req = Request(
                self.url,
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(req, timeout=self.timeout) as response:
                return response.status == 200
        except URLError as e:
            logger.error("Webhook error: %s", e)
            return False
        except Exception as e:
            logger.error("Error sending webhook: %s", e)
            return False


class NotificationManager:
    """Manage email and webhook notifications for remediation alerts."""

    def __init__(
        self,
        email_notifier: Optional[EmailNotifier] = None,
        webhook_notifier: Optional[WebhookNotifier] = None,
    ):
        self.email = email_notifier or EmailNotifier()
        self.webhook = webhook_notifier or WebhookNotifier()
        self.history: list[dict] = []

    def notify(
        self,
        alert_type: str,
        pod_name: str,
        risk_score: float,
        action_taken: str,
        details: str = "",
    ) -> tuple[bool, bool]:
        """Send notification via both email and webhook.

        Args:
            alert_type: Type of alert (e.g., "high_risk", "remediation", "prediction")
            pod_name: Name of the pod that triggered the alert
            risk_score: Risk score (0-100)
            action_taken: Remediation action taken (e.g., "pod_restart", "node_cordon")
            details: Additional details (JSON string or free text)

        Returns:
            (email_sent, webhook_sent) tuple of booleans
        """
        timestamp = _utc_now()

        # Email
        subject = f"{alert_type}: {pod_name}"
        body = (
            f"Predictive-Agent Alert\n"
            f"=======================\n\n"
            f"Timestamp: {timestamp}\n"
            f"Alert Type: {alert_type}\n"
            f"Pod: {pod_name}\n"
            f"Risk Score: {risk_score:.1f}\n"
            f"Action Taken: {action_taken}\n"
            f"Details: {details}\n"
        )
        email_sent = self.email.send(subject, body)

        # Webhook
        payload = {
            "alert_type": alert_type,
            "pod_name": pod_name,
            "risk_score": risk_score,
            "action_taken": action_taken,
            "details": details,
            "timestamp": timestamp,
        }
        webhook_sent = self.webhook.send(payload)

        # Record in history
        self.history.append(
            {
                "timestamp": timestamp,
                "alert_type": alert_type,
                "pod_name": pod_name,
                "risk_score": risk_score,
                "action_taken": action_taken,
                "email_sent": email_sent,
                "webhook_sent": webhook_sent,
            }
        )

        # Keep last 100 notifications
        if len(self.history) > 100:
            self.history = self.history[-100:]

        return email_sent, webhook_sent

    def get_history(self, limit: int = 20) -> list[dict]:
        """Return recent notification history."""
        return self.history[-limit:]


def create_notifier_from_config() -> NotificationManager:
    """Create a NotificationManager configured from environment variables.

    Reads os.environ at call time (not import time) for testability.
    """
    # Parse comma-separated email recipients
    alert_to = os.environ.get("ALERT_EMAIL_TO", "tobias.weiss@uni-marburg.de")
    to_addrs = [addr.strip() for addr in alert_to.split(",") if addr.strip()]

    email = EmailNotifier(
        host=os.environ.get("SMTP_HOST", "smtp.uni-marburg.de"),
        port=int(os.environ.get("SMTP_PORT", "587")),
        user=os.environ.get("SMTP_USER", ""),
        password=os.environ.get("SMTP_PASSWORD", ""),
        from_addr=os.environ.get("ALERT_EMAIL_FROM", "predictive-agent@opendesk.scs"),
        to_addrs=to_addrs,
        use_tls=os.environ.get("SMTP_USE_TLS", "true").lower() in ("true", "1", "yes"),
    )

    webhook = WebhookNotifier(
        url=os.environ.get("WEBHOOK_URL", ""),
        timeout=int(os.environ.get("WEBHOOK_TIMEOUT", "10")),
    )

    return NotificationManager(email_notifier=email, webhook_notifier=webhook)
