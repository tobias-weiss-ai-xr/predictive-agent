"""Docker Engine API collector over unix socket.

Collects container metrics from Docker Engine API via unix socket (AF_UNIX).
Uses stdlib socket + http.client style requests (no third-party dependencies).
Normalizes container data to the same shape consumed by state_model.update_pod().
"""

import http.client
import json
import re
import socket
import urllib.parse
from typing import Any, Dict, Optional

from predictive_agent import config

# Pre-compiled regex for error detection (same as collector.py)
_ERROR_RE = re.compile(
    r"\b(?:ERROR|Error|FATAL|PANIC|OOM|CrashLoopBackOff|Exception|Traceback)\b"
    r"|\bpanic:|\bfatal:"
)


class DockerSocketClient:
    """HTTP client for Docker Engine API over unix socket.
    
    Uses stdlib socket and http.client to communicate with Docker daemon
    over a unix domain socket. Mirrors the urllib style used in llm.py.
    """

    def __init__(self, socket_path: Optional[str] = None):
        """Initialize Docker socket client.
        
        Args:
            socket_path: Path to Docker socket (default from DOCKER_SOCKET env)
        """
        self.socket_path = socket_path or config.DOCKER_SOCKET
        self._connection: Optional[http.client.HTTPConnection] = None

    def _get_connection(self) -> http.client.HTTPConnection:
        """Get or create an HTTPConnection over unix socket.
        
        Returns:
            http.client.HTTPConnection connected to Docker socket
        
        Raises:
            FileNotFoundError: If socket path doesn't exist
            PermissionError: If socket is not accessible
            OSError: For other socket errors
        """
        if self._connection:
            return self._connection
        
        # Create a custom connection that uses AF_UNIX
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.connect(self.socket_path)
        except FileNotFoundError:
            raise FileNotFoundError(
                f"Docker socket not found: {self.socket_path}"
            )
        except PermissionError:
            raise PermissionError(
                f"Permission denied accessing Docker socket: {self.socket_path}"
            )
        except OSError as e:
            raise OSError(f"Failed to connect to Docker socket: {e}")
        
        # Create HTTPConnection using the connected socket
        # We use a dummy host since we're on unix socket
        self._connection = http.client.HTTPConnection("unix")
        self._connection.sock = sock
        return self._connection

    def request(
        self,
        method: str,
        path: str,
        data: Optional[bytes] = None,
        headers: Optional[Dict[str, str]] = None,
        timeout: int = 30,
    ) -> tuple[int, Dict[str, str], str]:
        """Make an HTTP request to Docker API.
        
        Args:
            method: HTTP method (GET, POST, etc.)
            path: API path (e.g., "/containers/json")
            data: Optional request body as bytes
            headers: Optional dict of HTTP headers
            timeout: Request timeout in seconds
        
        Returns:
            Tuple of (status_code, response_headers, response_body)
        
        Raises:
            ConnectionError: If cannot connect to Docker socket
            Exception: For other errors
        """
        try:
            conn = self._get_connection()
            conn.timeout = timeout
            
            # The server closes the connection after each response
            # (Connection: close). http.client will then try to re-connect to
            # the literal host string "unix", which fails with gaierror.
            # Re-establish the unix socket whenever the previous one is gone.
            if conn.sock is None or conn.sock.fileno() == -1:
                conn.close()
                self._connection = None
                conn = self._get_connection()
                conn.timeout = timeout
            
            default_headers = {
                "Host": "unix",
                "Accept": "application/json",
            }
            if headers:
                default_headers.update(headers)
            
            conn.request(method, path, body=data, headers=default_headers)
            response = conn.getresponse()
            
            status_code = response.status
            response_headers = dict(response.getheaders())
            response_body = response.read().decode("utf-8", errors="replace")
            
            return status_code, response_headers, response_body
            
        except socket.timeout:
            raise TimeoutError(f"Docker API request timed out after {timeout}s")
        except socket.error as e:
            raise ConnectionError(f"Docker socket error: {e}")
        except Exception as e:
            raise ConnectionError(f"Docker API request failed: {e}")

    def close(self) -> None:
        """Close the connection."""
        if self._connection:
            try:
                self._connection.close()
            except Exception:
                pass
            self._connection = None

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.close()
        return False


class DockerCollector:
    """Collect container metrics from Docker Engine API.
    
    Normalizes container data to the same shape consumed by state_model.update_pod().
    Filters containers by config.WATCH_NAMESPACES using com.docker.compose.project label.
    """

    def __init__(self, socket_path: Optional[str] = None):
        """Initialize Docker collector.
        
        Args:
            socket_path: Path to Docker socket (default from DOCKER_SOCKET env)
        """
        self.client = DockerSocketClient(socket_path)
        self._host_info: Optional[Dict[str, Any]] = None

    def _get_host_info(self) -> Dict[str, Any]:
        """Get Docker host info from /info endpoint.
        
        Caches the result for the lifetime of the collector.
        
        Returns:
            Dict with host memory and CPU information
        """
        if self._host_info is not None:
            return self._host_info
        
        try:
            status_code, _, body = self.client.request("GET", "/info")
            if status_code == 200 and body:
                self._host_info = json.loads(body)
        except Exception:
            self._host_info = {}
        
        return self._host_info

    def _get_namespace_from_container(self, container: Dict[str, Any]) -> str:
        """Extract namespace from container info.
        
        Uses com.docker.compose.project label as namespace.
        Falls back to first path component of container name, or 'default'.
        
        Args:
            container: Container dict from Docker API
        
        Returns:
            Namespace string
        """
        # Try com.docker.compose.project label first
        labels = container.get("Labels", {}) or {}
        project = labels.get("com.docker.compose.project", "")
        if project:
            return project
        
        # Fall back to container name
        names = container.get("Names", [])
        if names:
            # Names are like ["/project_container_1"]
            name = names[0].lstrip("/")
            # Use first path component (before first underscore or dash)
            if "_" in name:
                return name.split("_")[0]
            elif "-" in name:
                return name.split("-")[0]
            return name
        
        # Last resort
        return "default"

    def _get_container_name(self, container: Dict[str, Any]) -> str:
        """Extract the stable service name from a container.

        Compose containers are literally named ``{project}_{service}_{index}``
        (e.g. ``monitoring_predictive-agent_1``); the index changes on every
        recreate, so the state model must track the *service* name instead.
        Prefer the ``com.docker.compose.service`` label, then derive it by
        stripping the project prefix and numeric replica suffix.

        Args:
            container: Container dict from Docker API

        Returns:
            Normalized service name string
        """
        labels = container.get("Labels", {}) or {}
        service = labels.get("com.docker.compose.service", "")
        if service:
            return service

        names = container.get("Names", [])
        if names:
            # Remove leading slash
            name = names[0].lstrip("/")
            # Strip "{project}_" prefix and "_{index}" suffix from compose names.
            project = labels.get("com.docker.compose.project", "")
            if project:
                prefix = f"{project}_"
                if name.startswith(prefix):
                    name = name[len(prefix):]
            if re.search(r"_\d+$", name):
                name = re.sub(r"_\d+$", "", name)
            return name

        # Fall back to ID (short form)
        container_id = container.get("Id", "")
        if container_id:
            return container_id[:12]

        return "unknown"

    def _should_skip_namespace(self, namespace: str) -> bool:
        """Check if a namespace should be skipped.
        
        Args:
            namespace: Namespace to check
        
        Returns:
            True if namespace should be skipped
        """
        if namespace in config.SKIP_NAMESPACES:
            return True
        if config.WATCH_NAMESPACES and namespace not in config.WATCH_NAMESPACES:
            return True
        return False

    def _get_node_pressure(self) -> bool:
        """Check if host is under memory pressure.
        
        Uses host memory usage ratio from /info endpoint.
        Compares against DOCKER_HOST_PRESSURE_THRESHOLD.
        
        Returns:
            True if host memory usage ratio exceeds threshold
        """
        host_info = self._get_host_info()
        
        # Get memory stats from Docker info
        mem_total = host_info.get("MemTotal", 0)
        mem_used = host_info.get("Containers", {}).get("Memory", {}).get("TotalRSS", 0)
        
        # Alternative: Docker info provides memory stats differently
        # Try: DockerInfo.MemTotal and the used memory from containers
        if mem_total <= 0:
            return False
        
        # Docker info structure may vary; try different paths
        # In newer Docker: MemoryStats from /info
        container_stats = host_info.get("Containers", {})
        if isinstance(container_stats, dict):
            memory_stats = container_stats.get("Memory", {})
            if isinstance(memory_stats, dict):
                mem_used = memory_stats.get("TotalRSS", 0)
        
        # If we can't get used memory, check other indicators
        if mem_used <= 0:
            # Try DockerInfo.ContainerStats if available
            cont_stats = host_info.get("ContainerStats", {})
            if isinstance(cont_stats, dict):
                mem_used = cont_stats.get("MemoryTotal", 0)
        
        if mem_used <= 0:
            return False
        
        # Calculate usage ratio
        usage_ratio = mem_used / mem_total if mem_total > 0 else 0
        
        return usage_ratio > config.DOCKER_HOST_PRESSURE_THRESHOLD

    def list_containers(self) -> list:
        """List all containers from Docker API.
        
        Returns:
            List of container dicts from /containers/json?all=1
        """
        try:
            status_code, _, body = self.client.request("GET", "/containers/json?all=1")
            if status_code == 200 and body:
                return json.loads(body)
        except Exception:
            pass
        return []

    def get_container_details(self, container_id: str) -> Dict[str, Any]:
        """Get detailed info for a single container.
        
        Args:
            container_id: Container ID or name
        
        Returns:
            Container details dict from /containers/{id}/json
        """
        try:
            status_code, _, body = self.client.request(
                "GET", f"/containers/{urllib.parse.quote(container_id, safe='')}/json"
            )
            if status_code == 200 and body:
                return json.loads(body)
        except Exception:
            pass
        return {}

    def get_container_stats(self, container_id: str) -> Dict[str, Any]:
        """Get stats for a single container.
        
        Args:
            container_id: Container ID or name
        
        Returns:
            Container stats dict from /containers/{id}/stats?stream=false
        """
        try:
            status_code, _, body = self.client.request(
                "GET", f"/containers/{urllib.parse.quote(container_id, safe='')}/stats?stream=false"
            )
            if status_code == 200 and body:
                return json.loads(body)
        except Exception:
            pass
        return {}

    def get_container_logs(self, container_id: str, tail: int = 50) -> str:
        """Get logs for a single container.
        
        Args:
            container_id: Container ID or name
            tail: Number of lines to return
        
        Returns:
            Log text string
        """
        try:
            status_code, _, body = self.client.request(
                "GET", f"/containers/{urllib.parse.quote(container_id, safe='')}/logs?tail={tail}&stderr=1&stdout=1"
            )
            if status_code == 200:
                # Docker logs endpoint returns raw logs, not JSON
                # It includes header bytes that we need to skip
                # The response is a multiplexed stream with header: [stream type][size]
                # For simplicity, try to decode as text with error handling
                try:
                    return body.decode("utf-8", errors="replace")
                except (UnicodeDecodeError, AttributeError):
                    # If body is already a string
                    if isinstance(body, str):
                        return body
                    return ""
        except Exception:
            pass
        return ""

    def count_log_errors(self, log_text: str) -> int:
        """Count error-level log lines (same as collector.count_log_errors).
        
        Args:
            log_text: Raw log text
        
        Returns:
            Number of error lines found
        """
        count = 0
        for line in log_text.split("\n"):
            if _ERROR_RE.search(line):
                count += 1
        return count

    def collect(self) -> Dict[str, Dict[str, Any]]:
        """Collect metrics from all containers and normalize.
        
        Returns:
            Dict mapping ns/name to normalized metrics dict with keys:
            - namespace
            - name  
            - memory_mib
            - memory_limit_mib
            - cpu_m
            - restart_count
            - log_errors
            - node_pressure
        """
        containers = self.list_containers()
        node_pressure = self._get_node_pressure()
        
        results = {}
        
        for container in containers:
            container_id = container.get("Id", "")
            namespace = self._get_namespace_from_container(container)
            name = self._get_container_name(container)
            
            # Skip if namespace is not watched
            if self._should_skip_namespace(namespace):
                continue
            
            pod_key = f"{namespace}/{name}"
            
            # Get container details for memory limit and restart count
            details = self.get_container_details(container_id) if container_id else {}
            
            # Extract memory limit from HostConfig.Memory
            host_config = details.get("HostConfig", {}) or {}
            memory_bytes = host_config.get("Memory", 0) or 0
            memory_limit_mib = memory_bytes // (1024 * 1024)  # Convert to MiB
            
            # Extract restart count
            restart_count = details.get("RestartCount", 0) or 0
            
            # Get current stats for CPU and memory usage
            stats = self.get_container_stats(container_id) if container_id else {}
            
            # Extract CPU and memory from stats
            cpu_m = 0
            memory_mib = 0
            
            if stats:
                # Stats structure: {"read": "2025-01-01T00:00:00.000000000Z", ...}
                # The actual stats are at the top level or under a key
                cpu_stats = stats.get("cpu_stats", {})
                precpu_stats = stats.get("precpu_stats", {})
                memory_stats = stats.get("memory_stats", {})
                
                # Calculate CPU usage
                # CPU usage is calculated from cpu_stats.cpu_usage.total_usage
                # and precpu_stats.cpu_usage.total_usage
                cpu_usage = cpu_stats.get("cpu_usage", {}).get("total_usage", 0)
                precpu_usage = precpu_stats.get("cpu_usage", {}).get("total_usage", 0)
                
                # Get number of CPUs from cpu_stats
                cpu_delta = cpu_usage - precpu_usage
                system_cpu_usage = cpu_stats.get("system_cpu_usage", 0)
                precpu_system_cpu_usage = precpu_stats.get("system_cpu_usage", 0)
                system_cpu_delta = system_cpu_usage - precpu_system_cpu_usage
                
                # Online CPUs from cpu_stats
                online_cpus = cpu_stats.get("online_cpus", 1)
                
                if system_cpu_delta > 0 and online_cpus > 0:
                    # CPU percentage = (cpu_delta / system_cpu_delta) * online_cpus * 100
                    cpu_pct = (cpu_delta / system_cpu_delta) * online_cpus * 100
                    # Convert to millicores (100% = 1000m)
                    if cpu_pct > 0:
                        cpu_m = int(cpu_pct * 10)
                
                # Memory usage from memory_stats
                usage = memory_stats.get("usage", 0)
                if usage > 0:
                    memory_mib = usage // (1024 * 1024)  # Convert to MiB
            
            # Get log errors
            log_errors = 0
            container_state = container.get("State", "")
            
            # Only collect logs if container is not in skipped states
            # (similar to kubectl path)
            if container_state not in config.SKIP_LOGS_STATUSES:
                logs = self.get_container_logs(container_id)
                log_errors = self.count_log_errors(logs)
            
            # Build normalized record
            results[pod_key] = {
                "namespace": namespace,
                "name": name,
                "memory_mib": memory_mib,
                "memory_limit_mib": memory_limit_mib,
                "cpu_m": cpu_m,
                "restart_count": restart_count,
                "log_errors": log_errors,
                "node_pressure": node_pressure,
            }
        
        return results

    def close(self) -> None:
        """Close the underlying client connection."""
        self.client.close()


def collect_docker_metrics() -> Dict[str, Dict[str, Any]]:
    """Convenience function to collect Docker metrics.
    
    Creates a DockerCollector, collects metrics, and returns normalized records.
    Handles connection errors gracefully (returns empty dict).
    
    Returns:
        Dict mapping ns/name to normalized metrics dict
    """
    try:
        collector = DockerCollector()
        return collector.collect()
    except (FileNotFoundError, PermissionError, ConnectionError) as e:
        # Log once and return empty - don't crash the reconcile loop
        import logging
        logger = logging.getLogger("predictive-agent")
        logger.warning("Docker collector unavailable: %s", e)
        return {}
    except Exception as e:
        import logging
        logger = logging.getLogger("predictive-agent")
        logger.error("Docker collector error: %s", e)
        return {}
