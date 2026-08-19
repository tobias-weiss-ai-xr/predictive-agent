"""Tests for NodeCordonAction (REM-3)."""

import pytest
from unittest.mock import patch, MagicMock

from predictive_agent.remediator import ActionContext
from predictive_agent.actions.node_cordon import NodeCordonAction


class TestNodeCordonShouldExecute:
    """Test NodeCordonAction.should_execute()."""

    def _make_node_state(self, cpu_pct=50, mem_pct=50, healthy_nodes=3):
        return MagicMock(cpu_pct=cpu_pct, mem_pct=mem_pct, healthy_nodes=healthy_nodes)

    def test_high_cpu_triggers(self):
        action = NodeCordonAction()
        node = self._make_node_state(cpu_pct=95, mem_pct=50)
        assert action.should_execute(node, None, 85.0) is True

    def test_high_memory_triggers(self):
        action = NodeCordonAction()
        node = self._make_node_state(cpu_pct=50, mem_pct=95)
        assert action.should_execute(node, None, 85.0) is True

    def test_low_resource_does_not_trigger(self):
        action = NodeCordonAction()
        node = self._make_node_state(cpu_pct=50, mem_pct=50)
        assert action.should_execute(node, None, 85.0) is False

    def test_low_risk_does_not_trigger(self):
        action = NodeCordonAction()
        node = self._make_node_state(cpu_pct=95, mem_pct=95)
        assert action.should_execute(node, None, 70.0) is False

    def test_few_healthy_nodes_blocks(self):
        action = NodeCordonAction()
        node = self._make_node_state(cpu_pct=95, mem_pct=95, healthy_nodes=1)
        assert action.should_execute(node, None, 85.0) is False

    def test_exact_threshold_does_not_trigger(self):
        """CPU exactly at 90% should NOT trigger (uses >)."""
        action = NodeCordonAction()
        node = self._make_node_state(cpu_pct=90, mem_pct=50)
        assert action.should_execute(node, None, 85.0) is False

    def test_just_above_threshold_triggers(self):
        """CPU at 91% should trigger."""
        action = NodeCordonAction()
        node = self._make_node_state(cpu_pct=91, mem_pct=50)
        assert action.should_execute(node, None, 85.0) is True


class TestNodeCordonExecute:
    """Test NodeCordonAction.execute()."""

    @patch("predictive_agent.actions.node_cordon.subprocess.run")
    def test_execute_dry_run(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="node cordoned (dry run)", stderr="")
        action = NodeCordonAction()
        ctx = ActionContext(namespace="default", dry_run=True)
        result = action.execute("node-1", ctx)
        assert result.success is True
        assert result.dry_run is True
        assert result.action == "node_cordon"
        cmd = mock_run.call_args[0][0]
        assert "cordon" in cmd
        assert "node-1" in cmd
        assert "--dry-run=server" in cmd

    @patch("predictive_agent.actions.node_cordon.subprocess.run")
    def test_execute_real(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="node/node-1 cordoned", stderr="")
        action = NodeCordonAction()
        ctx = ActionContext(namespace="default", dry_run=False)
        result = action.execute("node-1", ctx)
        assert result.success is True
        assert result.dry_run is False
        cmd = mock_run.call_args[0][0]
        assert "--dry-run=server" not in cmd

    @patch("predictive_agent.actions.node_cordon.subprocess.run")
    def test_execute_failure(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="Error: node not found")
        action = NodeCordonAction()
        ctx = ActionContext(namespace="default", dry_run=False)
        result = action.execute("node-1", ctx)
        assert result.success is False
        assert "not found" in result.message

    @patch("predictive_agent.actions.node_cordon.subprocess.run")
    def test_execute_timeout(self, mock_run):
        import subprocess
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="kubectl", timeout=30)
        action = NodeCordonAction()
        ctx = ActionContext(namespace="default", dry_run=False)
        result = action.execute("node-1", ctx)
        assert result.success is False
        assert "timed out" in result.message

    @patch("predictive_agent.actions.node_cordon.subprocess.run")
    def test_execute_includes_drain_recommendation(self, mock_run):
        """Test that successful cordon includes manual drain recommendation."""
        mock_run.return_value = MagicMock(returncode=0, stdout="node/node-1 cordoned", stderr="")
        action = NodeCordonAction()
        ctx = ActionContext(namespace="default", dry_run=False)
        result = action.execute("node-1", ctx)
        assert "drain" in result.message.lower()

    @patch("predictive_agent.actions.node_cordon.subprocess.run")
    def test_execute_exception(self, mock_run):
        mock_run.side_effect = RuntimeError("unexpected")
        action = NodeCordonAction()
        ctx = ActionContext(namespace="default", dry_run=False)
        result = action.execute("node-1", ctx)
        assert result.success is False
        assert "unexpected" in result.message

    @patch("predictive_agent.actions.node_cordon.subprocess.run")
    def test_execute_timeout_30s(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="cordoned", stderr="")
        action = NodeCordonAction()
        ctx = ActionContext(namespace="default", dry_run=False)
        action.execute("node-1", ctx)
        kwargs = mock_run.call_args[1]
        assert kwargs["timeout"] == 30
