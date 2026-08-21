"""Test pod status signal collection from collector.get_pod_status_signals."""
import pytest
from predictive_agent.collector import get_pod_status_signals


def _make_pod(phase="Running", container_ready=True, wait_state=None,
               terminated=False, terminated_reason=None, restart_count=0,
               scheduled=True, initialized=True):
    """Build a minimal pod JSON object for testing."""
    container_status = {
        "name": "main",
        "ready": container_ready,
        "restartCount": restart_count,
    }
    if wait_state:
        container_status["state"] = {"waiting": {"reason": wait_state}}
    elif terminated:
        container_status["state"] = {"terminated": {"reason": terminated_reason or "Error"}}
    else:
        container_status["state"] = {"running": {}}

    if terminated_reason and not terminated:
        container_status["lastState"] = {"terminated": {"reason": terminated_reason}}

    conditions = []
    if initialized:
        conditions.append({"type": "Initialized", "status": "True"})
    else:
        conditions.append({"type": "Initialized", "status": "False"})
    if scheduled:
        conditions.append({"type": "PodScheduled", "status": "True"})
    else:
        conditions.append({"type": "PodScheduled", "status": "False"})

    return {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {"name": "test-pod", "namespace": "default"},
        "spec": {"nodeName": "node-1"},
        "status": {
            "phase": phase,
            "containerStatuses": [container_status],
            "conditions": conditions,
        },
    }


class TestGetPodStatusSignals:
    """Test get_pod_status_signals function."""

    def test_running_healthy_pod(self):
        """A healthy Running pod with ready container should return low-risk signals."""
        pod = _make_pod(phase="Running", container_ready=True)
        signals = get_pod_status_signals(pod)
        assert signals["pod_phase"] == "Running"
        assert signals["container_ready"] is True
        assert signals["wait_state"] is None
        assert signals["terminated"] is False
        assert signals["terminated_reason"] is None
        assert signals["restart_count"] == 0
        assert signals["pod_scheduled"] is True
        assert signals["pod_initialized"] is True

    def test_crash_loop_back_off(self):
        """CrashLoopBackOff should be captured as wait_state."""
        pod = _make_pod(phase="Running", container_ready=False, wait_state="CrashLoopBackOff")
        signals = get_pod_status_signals(pod)
        assert signals["wait_state"] == "CrashLoopBackOff"
        assert signals["container_ready"] is False

    def test_create_container_config_error(self):
        """CreateContainerConfigError should be captured."""
        pod = _make_pod(phase="Pending", container_ready=False, wait_state="CreateContainerConfigError")
        signals = get_pod_status_signals(pod)
        assert signals["wait_state"] == "CreateContainerConfigError"
        assert signals["pod_phase"] == "Pending"

    def test_image_pull_back_off(self):
        """ImagePullBackOff should be captured."""
        pod = _make_pod(phase="Pending", container_ready=False, wait_state="ImagePullBackOff")
        signals = get_pod_status_signals(pod)
        assert signals["wait_state"] == "ImagePullBackOff"

    def test_oom_killed(self):
        """OOMKilled should be captured as terminated with reason."""
        pod = _make_pod(phase="Running", terminated=True, terminated_reason="OOMKilled")
        signals = get_pod_status_signals(pod)
        assert signals["terminated"] is True
        assert signals["terminated_reason"] == "OOMKilled"

    def test_terminated_error(self):
        """Terminated with Error reason should be captured."""
        pod = _make_pod(phase="Running", terminated=True, terminated_reason="Error")
        signals = get_pod_status_signals(pod)
        assert signals["terminated"] is True
        assert signals["terminated_reason"] == "Error"

    def test_last_state_terminated(self):
        """Last terminated state should be captured even if currently running."""
        pod = _make_pod(phase="Running", container_ready=True, terminated_reason="OOMKilled")
        signals = get_pod_status_signals(pod)
        # terminated_reason should be set from lastState
        assert signals["terminated_reason"] == "OOMKilled"

    def test_restart_count(self):
        """Restart count should be summed across containers."""
        pod = _make_pod(restart_count=5)
        signals = get_pod_status_signals(pod)
        assert signals["restart_count"] == 5

    def test_restart_count_multiple_containers(self):
        """Restart count should sum across multiple containers."""
        pod = _make_pod(restart_count=3)
        # Add a second container
        pod["status"]["containerStatuses"].append({
            "name": "sidecar",
            "ready": True,
            "restartCount": 7,
            "state": {"running": {}},
        })
        signals = get_pod_status_signals(pod)
        assert signals["restart_count"] == 10

    def test_pending_phase(self):
        """Pending phase should be captured."""
        pod = _make_pod(phase="Pending", container_ready=False, scheduled=False)
        signals = get_pod_status_signals(pod)
        assert signals["pod_phase"] == "Pending"
        assert signals["pod_scheduled"] is False

    def test_failed_phase(self):
        """Failed phase should be captured."""
        pod = _make_pod(phase="Failed", container_ready=False, terminated=True, terminated_reason="Error")
        signals = get_pod_status_signals(pod)
        assert signals["pod_phase"] == "Failed"
        assert signals["terminated"] is True

    def test_succeeded_phase(self):
        """Succeeded phase should be captured."""
        pod = _make_pod(phase="Succeeded", container_ready=False, terminated=True, terminated_reason="Completed")
        signals = get_pod_status_signals(pod)
        assert signals["pod_phase"] == "Succeeded"
        assert signals["terminated"] is True
        assert signals["terminated_reason"] == "Completed"

    def test_not_scheduled(self):
        """Unscheduled pod should have pod_scheduled=False."""
        pod = _make_pod(phase="Pending", scheduled=False)
        signals = get_pod_status_signals(pod)
        assert signals["pod_scheduled"] is False

    def test_unknown_phase(self):
        """Unknown phase should be captured."""
        pod = _make_pod(phase="Unknown")
        signals = get_pod_status_signals(pod)
        assert signals["pod_phase"] == "Unknown"

    def test_empty_pod(self):
        """Empty pod JSON should return safe defaults."""
        pod = {"status": {}}
        signals = get_pod_status_signals(pod)
        assert signals["pod_phase"] == "Unknown"
        assert signals["container_ready"] is True
        assert signals["wait_state"] is None
        assert signals["terminated"] is False
        assert signals["restart_count"] == 0
