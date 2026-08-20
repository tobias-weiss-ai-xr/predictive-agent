"""Node cordon action: cordon nodes with sustained pressure.

Cordons nodes that have sustained high CPU/memory or disk pressure.
Does NOT drain — cordon only (draining is too risky for automated action).
Logs a recommendation to drain manually.
"""

from __future__ import annotations

import logging
import subprocess

from predictive_agent.remediator import ActionContext, RemediationAction, RemediationResult

logger = logging.getLogger(__name__)

# Thresholds
NODE_CPU_THRESHOLD = 90.0
NODE_MEM_THRESHOLD = 90.0
RISK_THRESHOLD = 0.8
MIN_HEALTHY_NODES = 2


class NodeCordonAction(RemediationAction):
    """Cordon a node with sustained pressure (marks it unschedulable)."""

    name = "node_cordon"

    def should_execute(self, node_state, prediction, risk_score: float) -> bool:
        """Check if node should be cordoned.

        Returns True when:
        - Node CPU > 90% or memory > 90%
        - risk_score > 0.8 (higher than pod restart — cordon is more disruptive)
        - At least 2 other healthy nodes available
        """
        if risk_score <= RISK_THRESHOLD:
            return False

        cpu_pct = getattr(node_state, "cpu_pct", 0)
        mem_pct = getattr(node_state, "mem_pct", 0)

        if cpu_pct <= NODE_CPU_THRESHOLD and mem_pct <= NODE_MEM_THRESHOLD:
            return False

        # Check minimum healthy nodes (safety: never cordon the last available node)
        healthy_nodes = getattr(node_state, "healthy_nodes", MIN_HEALTHY_NODES)
        if healthy_nodes < MIN_HEALTHY_NODES:
            return False

        return True

    def execute(self, target: str, context: ActionContext) -> RemediationResult:
        """Cordon the node via kubectl.

        In dry_run mode, uses --dry-run=server.
        Does NOT drain — only marks unschedulable.
        """
        cmd = ["kubectl", "cordon", target]
        if context.dry_run:
            cmd.append("--dry-run=server")

        cmd_str = " ".join(cmd)

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30,
            )
            success = result.returncode == 0
            message = result.stdout.strip() if success else result.stderr.strip()
            if not message:
                message = "node cordoned" if success else "kubectl cordon failed"
            if success:
                message += " — recommend manual drain: kubectl drain " + target + " --ignore-daemonsets --delete-emptydir-data"

            return RemediationResult(
                action=self.name,
                target=target,
                success=success,
                dry_run=context.dry_run,
                message=message,
                command=cmd_str,
            )
        except subprocess.TimeoutExpired:
            return RemediationResult(
                action=self.name,
                target=target,
                success=False,
                dry_run=context.dry_run,
                message="kubectl cordon timed out after 30s",
                command=cmd_str,
            )
        except Exception as e:
            return RemediationResult(
                action=self.name,
                target=target,
                success=False,
                dry_run=context.dry_run,
                message=f"error: {e}",
                command=cmd_str,
            )
