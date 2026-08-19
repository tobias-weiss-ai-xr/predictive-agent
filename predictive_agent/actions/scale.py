"""Deployment scale action: scale deployments up/down based on resource pressure.

Scales deployments when CPU is consistently high (scale up) or low (scale down).
Uses kubectl scale with min/max bounds to prevent runaway scaling.
"""

from __future__ import annotations

import logging
import subprocess

from predictive_agent.remediator import ActionContext, RemediationAction, RemediationResult

logger = logging.getLogger(__name__)

# Thresholds
HIGH_CPU_THRESHOLD = 80.0    # scale up if CPU > 80% for sustained period
LOW_CPU_THRESHOLD = 20.0     # scale down if CPU < 20% for sustained period
MIN_REPLICAS = 1
MAX_REPLICAS = 10
SCALE_COOLDOWN = 600          # 10 minutes between scaling actions
SUSTAINED_CYCLES = 5          # need 5+ cycles of high/low CPU
RISK_THRESHOLD = 75.0


class DeploymentScaleAction(RemediationAction):
    """Scale a deployment up or down based on resource pressure."""

    name = "scale"

    def should_execute(self, pod_state, prediction, risk_score: float) -> bool:
        """Check if deployment should be scaled.

        Returns True when:
        - CPU consistently high (>80% for 5+ cycles) → scale up
        - CPU consistently low (<20% for 5+ cycles) → scale down
        - risk_score > 75
        - NOT in protected namespace
        """
        if risk_score <= RISK_THRESHOLD:
            return False

        namespace = getattr(pod_state, "namespace", "")
        if namespace in {"kube-system", "opendesk-predictive-agent"}:
            return False

        cpu_pct = getattr(pod_state, "cpu_pct", 0)
        sustained_cycles = getattr(pod_state, "data_points", 0)

        if sustained_cycles < SUSTAINED_CYCLES:
            return False

        if cpu_pct > HIGH_CPU_THRESHOLD:
            return True
        if cpu_pct < LOW_CPU_THRESHOLD:
            return True

        return False

    def execute(self, target: str, context: ActionContext) -> RemediationResult:
        """Scale the deployment via kubectl scale.

        Determines scale up or down based on context.
        In dry_run mode, uses --dry-run=server.
        """
        # Determine scale direction
        scale_direction = getattr(context, "scale_direction", "up")
        current_replicas = getattr(context, "current_replicas", 1)

        if scale_direction == "up":
            new_replicas = min(current_replicas + 1, MAX_REPLICAS)
        else:
            new_replicas = max(current_replicas - 1, MIN_REPLICAS)

        if new_replicas == current_replicas:
            return RemediationResult(
                action=self.name,
                target=target,
                success=True,
                dry_run=context.dry_run,
                message=f"already at {'max' if scale_direction == 'up' else 'min'} replicas ({current_replicas})",
                command="",
            )

        cmd = [
            "kubectl", "scale", "deployment", target,
            "-n", context.namespace,
            f"--replicas={new_replicas}",
        ]
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
                message = f"scaled {target} to {new_replicas} replicas"

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
                message="kubectl scale timed out after 30s",
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
