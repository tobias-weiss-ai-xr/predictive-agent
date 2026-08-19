"""Remediation framework: RemediationAction ABC, RemediationManager, SafetyPolicy.

This module provides the core abstractions for autonomous self-healing:
- RemediationAction: base class for all remediation actions
- ActionContext: context passed to actions (namespace, dry_run, audit_log)
- RemediationResult: result of a remediation action
- SafetyPolicy: rate limiting, cooldowns, protected namespaces
- RemediationManager: registers actions, checks safety, executes with audit trail
"""

from __future__ import annotations

import logging
import os
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


def _utc_now() -> str:
    """Return current UTC time as ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ActionContext:
    """Context passed to every remediation action."""

    namespace: str = "opendesk-predictive-agent"
    dry_run: bool = True
    audit_log: list = field(default_factory=list)
    cooldowns: dict = field(default_factory=dict)  # target -> last_action_timestamp
    rate_limiter: dict = field(default_factory=dict)  # window -> list of timestamps


@dataclass
class RemediationResult:
    """Result of a remediation action."""

    action: str
    target: str
    success: bool
    dry_run: bool
    message: str
    timestamp: str = field(default_factory=_utc_now)
    command: str = ""


class SafetyPolicy:
    """Safety policy for remediation actions.

    Enforces rate limits, cooldowns, and protected namespaces.
    """

    def __init__(
        self,
        max_per_minute: int = 5,
        max_per_hour: int = 50,
        cooldown_seconds: int = 300,
        min_healthy_nodes: int = 2,
        protected_namespaces: Optional[set] = None,
    ):
        self.max_per_minute = max_per_minute
        self.max_per_hour = max_per_hour
        self.cooldown_seconds = cooldown_seconds
        self.min_healthy_nodes = min_healthy_nodes
        self.protected_namespaces = protected_namespaces or {
            "kube-system",
            "opendesk-predictive-agent",
        }

    def can_execute(
        self,
        target: str,
        action_type: str,
        context: ActionContext,
        namespace: str = "",
    ) -> tuple[bool, str]:
        """Check if an action can be executed under the safety policy.

        Returns (can_execute, reason).
        """
        now = time.time()

        # Check protected namespaces
        if namespace and namespace in self.protected_namespaces:
            return False, f"namespace '{namespace}' is protected"

        # Check cooldown
        cooldown_key = f"{action_type}:{target}"
        last_action = context.cooldowns.get(cooldown_key)
        if last_action is not None:
            elapsed = now - last_action
            if elapsed < self.cooldown_seconds:
                remaining = int(self.cooldown_seconds - elapsed)
                return False, f"cooldown active ({remaining}s remaining)"

        # Check per-minute rate limit
        minute_key = "minute"
        minute_actions = [t for t in context.rate_limiter.get(minute_key, []) if now - t < 60]
        if len(minute_actions) >= self.max_per_minute:
            return False, "per-minute rate limit exceeded"

        # Check per-hour rate limit
        hour_key = "hour"
        hour_actions = [t for t in context.rate_limiter.get(hour_key, []) if now - t < 3600]
        if len(hour_actions) >= self.max_per_hour:
            return False, "per-hour rate limit exceeded"

        return True, "ok"

    def record_action(
        self,
        target: str,
        action_type: str,
        context: ActionContext,
    ) -> None:
        """Record an action in the rate limiter and cooldown tracker."""
        now = time.time()
        cooldown_key = f"{action_type}:{target}"
        context.cooldowns[cooldown_key] = now

        # Update rate limiter
        minute_key = "minute"
        context.rate_limiter.setdefault(minute_key, [])
        context.rate_limiter[minute_key] = [
            t for t in context.rate_limiter[minute_key] if now - t < 60
        ] + [now]

        hour_key = "hour"
        context.rate_limiter.setdefault(hour_key, [])
        context.rate_limiter[hour_key] = [
            t for t in context.rate_limiter[hour_key] if now - t < 3600
        ] + [now]


class RemediationAction(ABC):
    """Base class for all remediation actions."""

    name: str = "base"

    @abstractmethod
    def should_execute(self, pod_state, prediction, risk_score: float) -> bool:
        """Check if this action should be taken for the given pod.

        Args:
            pod_state: PodTracker with current pod state
            prediction: PredictionResult with trend predictions
            risk_score: Bayesian risk score (0-100)

        Returns:
            True if this action should be executed
        """
        ...

    @abstractmethod
    def execute(self, target: str, context: ActionContext) -> RemediationResult:
        """Execute the remediation action.

        Args:
            target: Target resource name (pod name, node name, deployment name)
            context: ActionContext with namespace, dry_run, audit_log

        Returns:
            RemediationResult with action outcome
        """
        ...


class RemediationManager:
    """Manages remediation actions, safety policy, and audit trail.

    Registers actions, checks safety policy before execution,
    and maintains an audit trail of all actions taken.
    """

    def __init__(
        self,
        dry_run: bool = True,
        risk_threshold: float = 70.0,
        safety_policy: Optional[SafetyPolicy] = None,
    ):
        self.dry_run = dry_run
        self.risk_threshold = risk_threshold
        self.safety_policy = safety_policy or SafetyPolicy()
        self.actions: list[RemediationAction] = []
        self.audit_trail: list[RemediationResult] = []
        self.context = ActionContext(dry_run=dry_run)

    def register_action(self, action: RemediationAction) -> None:
        """Register a remediation action."""
        self.actions.append(action)
        logger.info("Registered remediation action: %s", action.name)

    def evaluate(
        self,
        pod_state,
        prediction,
        risk_score: float,
    ) -> list[RemediationResult]:
        """Evaluate all registered actions for a pod.

        Returns list of RemediationResult for actions that were executed.
        """
        results: list[RemediationResult] = []

        if risk_score < self.risk_threshold:
            logger.debug(
                "Risk score %.1f below threshold %.1f — skipping remediation",
                risk_score,
                self.risk_threshold,
            )
            return results

        for action in self.actions:
            try:
                if not action.should_execute(pod_state, prediction, risk_score):
                    continue

                target = getattr(pod_state, "name", str(pod_state))
                namespace = getattr(pod_state, "namespace", "")

                can_exec, reason = self.safety_policy.can_execute(
                    target, action.name, self.context, namespace
                )
                if not can_exec:
                    logger.info("Action %s skipped: %s", action.name, reason)
                    result = RemediationResult(
                        action=action.name,
                        target=target,
                        success=False,
                        dry_run=self.dry_run,
                        message=f"skipped: {reason}",
                    )
                    self.audit_trail.append(result)
                    results.append(result)
                    continue

                result = action.execute(target, self.context)
                self.safety_policy.record_action(target, action.name, self.context)
                self.audit_trail.append(result)
                self.context.audit_log.append(
                    {
                        "action": result.action,
                        "target": result.target,
                        "success": result.success,
                        "dry_run": result.dry_run,
                        "message": result.message,
                        "timestamp": result.timestamp,
                        "command": result.command,
                    }
                )
                results.append(result)
                logger.info(
                    "Remediation action %s on %s: success=%s dry_run=%s",
                    result.action,
                    result.target,
                    result.success,
                    result.dry_run,
                )
            except Exception as e:
                logger.exception("Remediation action %s failed: %s", action.name, e)
                result = RemediationResult(
                    action=action.name,
                    target=getattr(pod_state, "name", str(pod_state)),
                    success=False,
                    dry_run=self.dry_run,
                    message=f"error: {e}",
                )
                self.audit_trail.append(result)
                results.append(result)

        return results

    def get_audit_trail(self, limit: int = 20) -> list[dict]:
        """Return recent audit trail entries."""
        return self.context.audit_log[-limit:]

    def get_stats(self) -> dict:
        """Return remediation statistics."""
        total = len(self.audit_trail)
        successful = sum(1 for r in self.audit_trail if r.success)
        dry_run = sum(1 for r in self.audit_trail if r.dry_run)
        return {
            "total_actions": total,
            "successful_actions": successful,
            "failed_actions": total - successful,
            "dry_run_actions": dry_run,
            "registered_actions": [a.name for a in self.actions],
            "risk_threshold": self.risk_threshold,
            "dry_run": self.dry_run,
        }


def create_remediation_manager_from_config() -> RemediationManager:
    """Create a RemediationManager configured from environment variables."""
    enabled = os.environ.get("REMEDIATION_ENABLED", "false").lower() in ("true", "1", "yes")
    dry_run = os.environ.get("REMEDIATION_DRY_RUN", "true").lower() in ("true", "1", "yes")

    # If remediation is disabled, force dry_run
    if not enabled:
        dry_run = True

    protected_ns = {
        ns.strip()
        for ns in os.environ.get(
            "REMEDIATION_PROTECTED_NS", "kube-system,opendesk-predictive-agent"
        ).split(",")
        if ns.strip()
    }

    policy = SafetyPolicy(
        max_per_minute=int(os.environ.get("REMEDIATION_MAX_PER_MIN", "5")),
        max_per_hour=int(os.environ.get("REMEDIATION_MAX_PER_HOUR", "50")),
        cooldown_seconds=int(os.environ.get("REMEDIATION_COOLDOWN_S", "300")),
        protected_namespaces=protected_ns,
    )

    return RemediationManager(
        dry_run=dry_run,
        risk_threshold=float(os.environ.get("REMEDIATION_RISK_THRESHOLD", "70.0")),
        safety_policy=policy,
    )
