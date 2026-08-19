"""Tests for the email and webhook notifier (REM-6)."""

import os
import json
import pytest
from unittest.mock import patch, MagicMock, call

from predictive_agent.notifier import (
    EmailNotifier,
    WebhookNotifier,
    NotificationManager,
    create_notifier_from_config,
)


class TestEmailNotifier:
    """Test EmailNotifier with mocked SMTP."""

    def _make_notifier(self, **kwargs):
        defaults = dict(
            host="smtp.uni-marburg.de", port=587, user="", password="",
            from_addr="predictive-agent@opendesk.scs",
            to_addrs=["tobias.weiss@uni-marburg.de"], use_tls=True,
        )
        defaults.update(kwargs)
        return EmailNotifier(**defaults)

    @patch("predictive_agent.notifier.smtplib.SMTP")
    def test_send_basic(self, mock_smtp):
        """Test basic email sending with STARTTLS."""
        server = MagicMock()
        mock_smtp.return_value.__enter__.return_value = server
        notifier = self._make_notifier()
        result = notifier.send("Test Alert", "Body text")
        assert result is True
        mock_smtp.assert_called_once_with("smtp.uni-marburg.de", 587, timeout=10)
        server.starttls.assert_called_once()
        # No login when user/password empty
        server.login.assert_not_called()
        server.sendmail.assert_called_once()

    @patch("predictive_agent.notifier.smtplib.SMTP")
    def test_send_with_auth(self, mock_smtp):
        """Test email sending with SMTP auth."""
        server = MagicMock()
        mock_smtp.return_value.__enter__.return_value = server
        notifier = self._make_notifier(user="user@uni-marburg.de", password="secret")
        result = notifier.send("Test", "Body")
        assert result is True
        server.starttls.assert_called_once()
        server.login.assert_called_once_with("user@uni-marburg.de", "secret")

    @patch("predictive_agent.notifier.smtplib.SMTP")
    def test_send_no_tls(self, mock_smtp):
        """Test email sending without TLS."""
        server = MagicMock()
        mock_smtp.return_value.__enter__.return_value = server
        notifier = self._make_notifier(use_tls=False)
        result = notifier.send("Test", "Body")
        assert result is True
        server.starttls.assert_not_called()

    @patch("predictive_agent.notifier.smtplib.SMTP")
    def test_send_subject_format(self, mock_smtp):
        """Test that subject is prefixed with [SCS-Predictive-Agent]."""
        server = MagicMock()
        mock_smtp.return_value.__enter__.return_value = server
        notifier = self._make_notifier()
        notifier.send("High Risk", "Body")
        # Check sendmail was called with proper subject
        sendmail_args = server.sendmail.call_args
        msg_str = sendmail_args[0][2]
        assert "[SCS-Predictive-Agent] High Risk" in msg_str

    @patch("predictive_agent.notifier.smtplib.SMTP")
    def test_send_to_multiple_recipients(self, mock_smtp):
        """Test sending to multiple recipients."""
        server = MagicMock()
        mock_smtp.return_value.__enter__.return_value = server
        notifier = self._make_notifier(
            to_addrs=["tobias.weiss@uni-marburg.de", "admin@scs.example"]
        )
        result = notifier.send("Test", "Body")
        assert result is True
        sendmail_args = server.sendmail.call_args
        recipients = sendmail_args[0][1]
        assert "tobias.weiss@uni-marburg.de" in recipients
        assert "admin@scs.example" in recipients

    @patch("predictive_agent.notifier.smtplib.SMTP")
    def test_send_smtp_exception_returns_false(self, mock_smtp):
        """Test that SMTPException returns False, not raises."""
        import smtplib
        mock_smtp.side_effect = smtplib.SMTPException("Connection refused")
        notifier = self._make_notifier()
        result = notifier.send("Test", "Body")
        assert result is False

    @patch("predictive_agent.notifier.smtplib.SMTP")
    def test_send_timeout_returns_false(self, mock_smtp):
        """Test that timeout returns False."""
        import socket
        mock_smtp.side_effect = socket.timeout("timed out")
        notifier = self._make_notifier()
        result = notifier.send("Test", "Body")
        assert result is False

    @patch("predictive_agent.notifier.smtplib.SMTP")
    def test_send_generic_exception_returns_false(self, mock_smtp):
        """Test that any exception returns False."""
        mock_smtp.side_effect = Exception("unexpected error")
        notifier = self._make_notifier()
        result = notifier.send("Test", "Body")
        assert result is False

    @patch("predictive_agent.notifier.smtplib.SMTP")
    def test_default_recipient_is_tobias(self, mock_smtp):
        """Test that default recipient is tobias.weiss@uni-marburg.de."""
        notifier = EmailNotifier()
        assert "tobias.weiss@uni-marburg.de" in notifier.to_addrs

    @patch("predictive_agent.notifier.smtplib.SMTP")
    def test_email_body_contains_alert_details(self, mock_smtp):
        """Test that email body contains timestamp, pod name, risk score, action."""
        server = MagicMock()
        mock_smtp.return_value.__enter__.return_value = server
        notifier = self._make_notifier()
        notifier.send("High Risk", "Pod: api-server-abc\nRisk: 85.5\nAction: pod_restart")
        sendmail_args = server.sendmail.call_args
        msg_str = sendmail_args[0][2]
        assert "High Risk" in msg_str
        assert "api-server-abc" in msg_str
        assert "85.5" in msg_str
        assert "pod_restart" in msg_str


class TestWebhookNotifier:
    """Test WebhookNotifier with mocked urllib."""

    @patch("predictive_agent.notifier.urlopen")
    def test_send_basic(self, mock_urlopen):
        """Test basic webhook send."""
        response = MagicMock()
        response.status = 200
        mock_urlopen.return_value.__enter__.return_value = response
        notifier = WebhookNotifier(url="http://example.com/webhook")
        result = notifier.send({"alert": "test", "risk": 85})
        assert result is True
        mock_urlopen.assert_called_once()

    @patch("predictive_agent.notifier.urlopen")
    def test_send_json_payload(self, mock_urlopen):
        """Test that webhook sends JSON payload."""
        response = MagicMock()
        response.status = 200
        mock_urlopen.return_value.__enter__.return_value = response
        notifier = WebhookNotifier(url="http://example.com/webhook")
        payload = {"alert_type": "high_risk", "pod": "api-server", "risk_score": 90.5}
        notifier.send(payload)
        call_args = mock_urlopen.call_args
        req = call_args[0][0]
        assert req.method == "POST"
        assert req.headers.get("Content-type") == "application/json"
        data = json.loads(req.data.decode("utf-8"))
        assert data["alert_type"] == "high_risk"
        assert data["pod"] == "api-server"
        assert data["risk_score"] == 90.5

    def test_send_empty_url_returns_true(self):
        """Test that empty URL returns True (no-op)."""
        notifier = WebhookNotifier(url="")
        result = notifier.send({"alert": "test"})
        assert result is True

    @patch("predictive_agent.notifier.urlopen")
    def test_send_url_error_returns_false(self, mock_urlopen):
        """Test that URLError returns False."""
        from urllib.error import URLError
        mock_urlopen.side_effect = URLError("Connection refused")
        notifier = WebhookNotifier(url="http://example.com/webhook")
        result = notifier.send({"alert": "test"})
        assert result is False

    @patch("predictive_agent.notifier.urlopen")
    def test_send_non_200_returns_false(self, mock_urlopen):
        """Test that non-200 status returns False."""
        response = MagicMock()
        response.status = 500
        mock_urlopen.return_value.__enter__.return_value = response
        notifier = WebhookNotifier(url="http://example.com/webhook")
        result = notifier.send({"alert": "test"})
        assert result is False

    @patch("predictive_agent.notifier.urlopen")
    def test_send_timeout(self, mock_urlopen):
        """Test that timeout is passed to urlopen."""
        response = MagicMock()
        response.status = 200
        mock_urlopen.return_value.__enter__.return_value = response
        notifier = WebhookNotifier(url="http://example.com/webhook", timeout=30)
        notifier.send({"alert": "test"})
        call_kwargs = mock_urlopen.call_args[1]
        assert call_kwargs["timeout"] == 30


class TestNotificationManager:
    """Test NotificationManager."""

    def _make_manager(self):
        email = MagicMock(spec=EmailNotifier)
        email.send.return_value = True
        webhook = MagicMock(spec=WebhookNotifier)
        webhook.send.return_value = True
        return NotificationManager(email_notifier=email, webhook_notifier=webhook), email, webhook

    def test_notify_calls_both(self):
        """Test that notify() calls both email and webhook."""
        manager, email, webhook = self._make_manager()
        email_sent, webhook_sent = manager.notify(
            "high_risk", "api-server-abc", 85.5, "pod_restart", "Pod in CrashLoopBackOff"
        )
        assert email_sent is True
        assert webhook_sent is True
        email.send.assert_called_once()
        webhook.send.assert_called_once()

    def test_notify_email_subject(self):
        """Test that email subject contains alert type and pod name."""
        manager, email, webhook = self._make_manager()
        manager.notify("remediation", "pod-1", 90.0, "node_cordon")
        call_args = email.send.call_args
        subject = call_args[0][0]
        assert "remediation" in subject
        assert "pod-1" in subject

    def test_notify_email_body_contains_details(self):
        """Test that email body contains all alert details."""
        manager, email, webhook = self._make_manager()
        manager.notify("high_risk", "api-server", 85.5, "pod_restart", "CrashLoopBackOff")
        call_args = email.send.call_args
        body = call_args[0][1]
        assert "api-server" in body
        assert "85.5" in body
        assert "pod_restart" in body
        assert "CrashLoopBackOff" in body

    def test_notify_webhook_payload(self):
        """Test that webhook payload contains all fields."""
        manager, email, webhook = self._make_manager()
        manager.notify("high_risk", "api-server", 85.5, "pod_restart", "details")
        call_args = webhook.send.call_args
        payload = call_args[0][0]
        assert payload["alert_type"] == "high_risk"
        assert payload["pod_name"] == "api-server"
        assert payload["risk_score"] == 85.5
        assert payload["action_taken"] == "pod_restart"
        assert payload["details"] == "details"
        assert "timestamp" in payload

    def test_notify_history(self):
        """Test that notifications are recorded in history."""
        manager, email, webhook = self._make_manager()
        for i in range(5):
            manager.notify("alert", f"pod-{i}", 80.0, "action")
        history = manager.get_history()
        assert len(history) == 5
        assert history[0]["pod_name"] == "pod-0"
        assert history[4]["pod_name"] == "pod-4"

    def test_notify_history_limit(self):
        """Test that history is capped at 100 entries."""
        manager, email, webhook = self._make_manager()
        for i in range(105):
            manager.notify("alert", f"pod-{i}", 80.0, "action")
        history = manager.get_history(limit=200)
        assert len(history) == 100

    def test_notify_email_failure(self):
        """Test that email failure is recorded."""
        email = MagicMock(spec=EmailNotifier)
        email.send.return_value = False
        webhook = MagicMock(spec=WebhookNotifier)
        webhook.send.return_value = True
        manager = NotificationManager(email_notifier=email, webhook_notifier=webhook)
        email_sent, webhook_sent = manager.notify("alert", "pod-1", 80.0, "action")
        assert email_sent is False
        assert webhook_sent is True
        assert manager.history[-1]["email_sent"] is False
        assert manager.history[-1]["webhook_sent"] is True


class TestCreateNotifierFromConfig:
    """Test create_notifier_from_config()."""

    def test_defaults(self):
        with patch.dict(os.environ, {
            "ALERT_EMAIL_TO": "tobias.weiss@uni-marburg.de",
            "ALERT_EMAIL_FROM": "predictive-agent@opendesk.scs",
            "SMTP_HOST": "smtp.uni-marburg.de",
            "SMTP_PORT": "587",
            "SMTP_USER": "",
            "SMTP_PASSWORD": "",
            "SMTP_USE_TLS": "true",
            "WEBHOOK_URL": "",
            "WEBHOOK_TIMEOUT": "10",
        }):
            manager = create_notifier_from_config()
            assert manager.email.host == "smtp.uni-marburg.de"
            assert manager.email.port == 587
            assert "tobias.weiss@uni-marburg.de" in manager.email.to_addrs
            assert manager.email.use_tls is True
            assert manager.webhook.url == ""

    def test_multiple_recipients(self):
        with patch.dict(os.environ, {
            "ALERT_EMAIL_TO": "tobias.weiss@uni-marburg.de,admin@scs.example",
            "SMTP_HOST": "smtp.uni-marburg.de",
            "SMTP_PORT": "587",
            "SMTP_USE_TLS": "true",
            "WEBHOOK_URL": "",
        }):
            manager = create_notifier_from_config()
            assert len(manager.email.to_addrs) == 2
            assert "tobias.weiss@uni-marburg.de" in manager.email.to_addrs
            assert "admin@scs.example" in manager.email.to_addrs

    def test_webhook_url(self):
        with patch.dict(os.environ, {
            "ALERT_EMAIL_TO": "test@example.com",
            "SMTP_HOST": "smtp.example.com",
            "SMTP_PORT": "587",
            "WEBHOOK_URL": "http://webhook.example.com/alert",
            "WEBHOOK_TIMEOUT": "30",
        }):
            manager = create_notifier_from_config()
            assert manager.webhook.url == "http://webhook.example.com/alert"
            assert manager.webhook.timeout == 30
