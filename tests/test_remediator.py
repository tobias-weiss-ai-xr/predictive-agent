"""Tests for the remediation framework (REM-1)."""

import os
import time
import pytest
from unittest.mock import patch, MagicMock

from predictive_agent.remediator import (
    ActionContext,
    RemediationAction,
    RemediationManager,
    RemediationResult,
    SafetyPolicy,
    create_remediation_manager_from_config,
)


class TestRemediationActionABC:
    """Test that RemediationAction is an abstract base class."""

    def test_cannot_instantiate_abc_directly(self):
        """RemediationAction is abstract and cannot be instantiated."""
        with pytest.raises(TypeError):
            RemediationAction()

    def test_subclass_must_implement_methods(self):
        """Subclass must implement should_execute and execute."""

        class IncompleteAction(RemediationAction):
            name = "incomplete"

        with pytest.raises(TypeError):
            IncompleteAction()

    def test_subclass_with_implementations_works(self):
        """A complete subclass can be instantiated."""

        class TestAction(RemediationAction):
            name = "test"
            def should_execute(self, pod_state, prediction, risk_score):
                return risk_score > 50
            def execute(self, target, context):
                return RemediationResult(
                    action=self.name, target=target, success=True,
                    dry_run=context.dry_run, message="test action"
                )

        action = TestAction()
        assert action.name == "test"
        assert action.should_execute(None, None, 60) is True
        assert action.should_execute(None, None, 40) is False


class TestActionContext:
    """Test ActionContext defaults and behavior."""

    def test_defaults(self):
        ctx = ActionContext()
        assert ctx.namespace == "opendesk-predictive-agent"
        assert ctx.dry_run is True
        assert ctx.audit_log == []
        assert ctx.cooldowns == {}
        assert ctx.rate_limiter == {}

    def test_custom_values(self):
        ctx = ActionContext(namespace="custom", dry_run=False)
        assert ctx.namespace == "custom"
        assert ctx.dry_run is False


class TestRemediationResult:
    """Test RemediationResult dataclass."""

    def test_fields(self):
        result = RemediationResult(
            action="pod_restart", target="pod-1", success=True,
            dry_run=False, message="deleted", command="kubectl delete pod pod-1"
        )
        assert result.action == "pod_restart"
        assert result.target == "pod-1"
        assert result.success is True
        assert result.dry_run is False
        assert result.message == "deleted"
        assert result.command == "kubectl delete pod pod-1"
        assert result.timestamp  # auto-generated

    def test_timestamp_auto_generated(self):
        result = RemediationResult(
            action="test", target="t", success=True, dry_run=True, message="m"
        )
        assert result.timestamp is not None
        assert "T" in result.timestamp  # ISO format


class TestSafetyPolicy:
    """Test SafetyPolicy rate limiting, cooldowns, and protected namespaces."""

    def test_protected_namespace_blocked(self):
        policy = SafetyPolicy(protected_namespaces={"kube-system"})
        ctx = ActionContext()
        can, reason = policy.can_execute("pod-1", "pod_restart", ctx, "kube-system")
        assert can is False
        assert "protected" in reason

    def test_non_protected_namespace_allowed(self):
        policy = SafetyPolicy(protected_namespaces={"kube-system"})
        ctx = ActionContext()
        can, reason = policy.can_execute("pod-1", "pod_restart", ctx, "default")
        assert can is True
        assert reason == "ok"

    def test_cooldown_blocks_repeated_action(self):
        policy = SafetyPolicy(cooldown_seconds=60)
        ctx = ActionContext()
        # First action: allowed
        can1, _ = policy.can_execute("pod-1", "pod_restart", ctx)
        assert can1 is True
        policy.record_action("pod-1", "pod_restart", ctx)
        # Second action immediately: blocked by cooldown
        can2, reason2 = policy.can_execute("pod-1", "pod_restart", ctx)
        assert can2 is False
        assert "cooldown" in reason2

    def test_cooldown_expires(self):
        policy = SafetyPolicy(cooldown_seconds=1)
        ctx = ActionContext()
        policy.record_action("pod-1", "pod_restart", ctx)
        # Wait for cooldown to expire
        time.sleep(1.1)
        can, reason = policy.can_execute("pod-1", "pod_restart", ctx)
        assert can is True

    def test_per_minute_rate_limit(self):
        policy = SafetyPolicy(max_per_minute=3)
        ctx = ActionContext()
        for i in range(3):
            can, _ = policy.can_execute(f"pod-{i}", "pod_restart", ctx)
            assert can is True
            policy.record_action(f"pod-{i}", "pod_restart", ctx)
        # 4th action: blocked
        can, reason = policy.can_execute("pod-4", "pod_restart", ctx)
        assert can is False
        assert "rate limit" in reason.lower()

    def test_per_hour_rate_limit(self):
        policy = SafetyPolicy(max_per_hour=2)
        ctx = ActionContext()
        for i in range(2):
            can, _ = policy.can_execute(f"pod-{i}", "pod_restart", ctx)
            assert can is True
            policy.record_action(f"pod-{i}", "pod_restart", ctx)
        # 3rd action: blocked
        can, reason = policy.can_execute("pod-3", "pod_restart", ctx)
        assert can is False
        assert "rate limit" in reason.lower()

    def test_different_targets_not_blocked_by_cooldown(self):
        policy = SafetyPolicy(cooldown_seconds=60)
        ctx = ActionContext()
        policy.record_action("pod-1", "pod_restart", ctx)
        # Different target: not blocked
        can, _ = policy.can_execute("pod-2", "pod_restart", ctx)
        assert can is True

    def test_different_action_types_not_blocked_by_cooldown(self):
        policy = SafetyPolicy(cooldown_seconds=60)
        ctx = ActionContext()
        policy.record_action("pod-1", "pod_restart", ctx)
        # Different action type: not blocked
        can, _ = policy.can_execute("pod-1", "node_cordon", ctx)
        assert can is True

    def test_default_protected_namespaces(self):
        policy = SafetyPolicy()
        assert "kube-system" in policy.protected_namespaces
        assert "opendesk-predictive-agent" in policy.protected_namespaces


class TestRemediationManager:
    """Test RemediationManager registration, evaluation, and audit trail."""

    def _make_action(self, name="test_action", should_exec=True, exec_result=None):
        class TestAction(RemediationAction):
            def __init__(self):
                self.name = name
            def should_execute(self, pod_state, prediction, risk_score):
                return should_exec
            def execute(self, target, context):
                if exec_result:
                    return exec_result
                return RemediationResult(
                    action=self.name, target=target, success=True,
                    dry_run=context.dry_run, message="executed"
                )
        return TestAction()

    def test_register_action(self):
        manager = RemediationManager()
        action = self._make_action()
        manager.register_action(action)
        assert len(manager.actions) == 1
        assert manager.actions[0].name == "test_action"

    def test_evaluate_below_threshold(self):
        manager = RemediationManager(risk_threshold=70.0)
        manager.register_action(self._make_action())
        # Risk below threshold: no actions
        results = manager.evaluate(None, None, 50.0)
        assert results == []

    def test_evaluate_above_threshold(self):
        manager = RemediationManager(risk_threshold=70.0)
        manager.register_action(self._make_action())
        results = manager.evaluate(None, None, 80.0)
        assert len(results) == 1
        assert results[0].success is True

    def test_evaluate_action_not_triggered(self):
        manager = RemediationManager(risk_threshold=70.0)
        manager.register_action(self._make_action(should_exec=False))
        results = manager.evaluate(None, None, 80.0)
        assert results == []

    def test_audit_trail(self):
        manager = RemediationManager(risk_threshold=70.0)
        manager.register_action(self._make_action())
        manager.evaluate(None, None, 80.0)
        assert len(manager.audit_trail) == 1
        assert manager.audit_trail[0].action == "test_action"

    def test_dry_run_mode(self):
        manager = RemediationManager(dry_run=True, risk_threshold=70.0)
        manager.register_action(self._make_action())
        results = manager.evaluate(None, None, 80.0)
        assert results[0].dry_run is True

    def test_get_audit_trail_limit(self):
        # Use a policy with no cooldown to allow multiple actions on different targets
        policy = SafetyPolicy(cooldown_seconds=0, max_per_minute=100, max_per_hour=1000)
        manager = RemediationManager(risk_threshold=0.0, safety_policy=policy)
        manager.register_action(self._make_action())
        for i in range(25):
            manager.evaluate(MagicMock(name=f"pod-{i}", namespace="default"), None, 80.0)
        trail = manager.get_audit_trail(limit=10)
        assert len(trail) == 10

    def test_get_stats(self):
        manager = RemediationManager(dry_run=True, risk_threshold=70.0)
        manager.register_action(self._make_action())
        manager.evaluate(None, None, 80.0)
        stats = manager.get_stats()
        assert stats["total_actions"] == 1
        assert stats["successful_actions"] == 1
        assert stats["dry_run_actions"] == 1
        assert "test_action" in stats["registered_actions"]

    def test_evaluate_exception_handling(self):
        manager = RemediationManager(risk_threshold=70.0)

        class FailingAction(RemediationAction):
            name = "failing"
            def should_execute(self, pod_state, prediction, risk_score):
                return True
            def execute(self, target, context):
                raise RuntimeError("boom")

        manager.register_action(FailingAction())
        results = manager.evaluate(None, None, 80.0)
        assert len(results) == 1
        assert results[0].success is False
        assert "boom" in results[0].message


class TestCreateFromConfig:
    """Test create_remediation_manager_from_config()."""

    def test_defaults(self):
        with patch.dict(os.environ, {
            "REMEDIATION_ENABLED": "false",
            "REMEDIATION_DRY_RUN": "true",
            "REMEDIATION_MAX_PER_MIN": "5",
            "REMEDIATION_MAX_PER_HOUR": "50",
            "REMEDIATION_COOLDOWN_S": "300",
            "REMEDIATION_RISK_THRESHOLD": "70.0",
            "REMEDIATION_PROTECTED_NS": "kube-system,opendesk-predictive-agent",
        }):
            manager = create_remediation_manager_from_config()
            assert manager.dry_run is True
            assert manager.risk_threshold == 70.0
            assert "kube-system" in manager.safety_policy.protected_namespaces

    def test_enabled_with_dry_run_false(self):
        with patch.dict(os.environ, {
            "REMEDIATION_ENABLED": "true",
            "REMEDIATION_DRY_RUN": "false",
            "REMEDIATION_MAX_PER_MIN": "5",
            "REMEDIATION_MAX_PER_HOUR": "50",
            "REMEDIATION_COOLDOWN_S": "300",
            "REMEDIATION_RISK_THRESHOLD": "70.0",
            "REMEDIATION_PROTECTED_NS": "kube-system",
        }):
            manager = create_remediation_manager_from_config()
            assert manager.dry_run is False

    def test_disabled_forces_dry_run(self):
        with patch.dict(os.environ, {
            "REMEDIATION_ENABLED": "false",
            "REMEDIATION_DRY_RUN": "false",
            "REMEDIATION_MAX_PER_MIN": "5",
            "REMEDIATION_MAX_PER_HOUR": "50",
            "REMEDIATION_COOLDOWN_S": "300",
            "REMEDIATION_RISK_THRESHOLD": "70.0",
            "REMEDIATION_PROTECTED_NS": "kube-system",
        }):
            manager = create_remediation_manager_from_config()
            assert manager.dry_run is True  # forced dry_run when disabled
