"""Knowledge graph integration for impact-aware risk scoring.

Queries the SCS Dgraph knowledge graph to determine the blast radius of a pod
— how many other pods, services, and namespaces depend on it. This feeds into
the risk scorer to escalate risk for pods with high dependency impact.

If Dgraph is unavailable, all queries return 0 (graceful degradation).
"""

import json
import logging
import os
import urllib.request
import urllib.error
from typing import Optional

logger = logging.getLogger(__name__)

DGRAPH_URL = os.environ.get("DGRAPH_URL", "http://127.0.0.1:8081")
BLAST_RADIUS_CACHE_SECONDS = int(os.environ.get("BLAST_RADIUS_CACHE_SECONDS", "300"))


class KnowledgeGraphClient:
    """Thin Dgraph HTTP client for blast-radius queries.

    Uses the Dgraph /query endpoint with DQL. Falls back gracefully when
    Dgraph is not running.
    """

    def __init__(self, url: str = DGRAPH_URL, timeout: int = 5):
        self.url = url.rstrip("/")
        self.timeout = timeout
        self._cache: dict[str, tuple[float, int]] = {}  # pod_key -> (timestamp, blast_radius)

    def _query(self, query: str) -> Optional[dict]:
        """Execute a DQL query against Dgraph. Returns None on error."""
        try:
            data = json.dumps({"query": query}).encode()
            req = urllib.request.Request(
                f"{self.url}/query",
                data=data,
                headers={"Content-Type": "application/dql"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode())
        except (urllib.error.URLError, json.JSONDecodeError, OSError) as e:
            logger.debug("Dgraph query failed: %s", e)
            return None

    def get_blast_radius(self, pod_key: str) -> int:
        """Get the blast radius (dependent count) for a pod.

        This queries the knowledge graph for all nodes that depend on the
        given pod, either directly or transitively. Returns 0 if Dgraph is
        unavailable or the pod is not in the graph.

        Args:
            pod_key: Pod identifier (namespace/name)

        Returns:
            Number of dependent nodes (pods, services, namespaces).
        """
        # Check cache
        cached = self._cache.get(pod_key)
        if cached is not None:
            ts, radius = cached
            import time as _t
            if _t.time() - ts < BLAST_RADIUS_CACHE_SECONDS:
                return radius

        # Query Dgraph for dependent nodes
        # We look for K8sPod nodes by name and count their dependents
        namespace, _, name = pod_key.partition("/")
        dql = f"""{{
            pods(func: type(K8sPod)) @filter(eq(k8s_pod_name, "{name}")) {{
                uid
                k8s_pod_name
                k8s_pod_namespace
                ~pod_node {{
                    uid
                    k8s_pod_name
                }}
                ~depends_on {{
                    uid
                    dgraph.type
                }}
            }}
        }}"""

        result = self._query(dql)
        if result is None:
            self._cache[pod_key] = (0.0, 0)
            return 0

        pods = result.get("data", {}).get("pods", [])
        if not pods:
            self._cache[pod_key] = (0.0, 0)
            return 0

        # Count all dependent nodes (deduplicate by uid)
        dependent_uids = set()
        for pod in pods:
            for dep in pod.get("~pod_node", []):
                dependent_uids.add(dep.get("uid", ""))
            for dep in pod.get("~depends_on", []):
                dependent_uids.add(dep.get("uid", ""))

        radius = len(dependent_uids)
        import time as _t
        self._cache[pod_key] = (_t.time(), radius)
        return radius

    def get_node_blast_radius(self, node_name: str) -> int:
        """Get blast radius for a node (all pods on that node)."""
        dql = f"""{{
            pods(func: type(K8sPod)) @filter(eq(k8s_pod_node, "{node_name}")) {{
                uid
                k8s_pod_name
                k8s_pod_namespace
            }}
        }}"""

        result = self._query(dql)
        if result is None:
            return 0

        pods = result.get("data", {}).get("pods", [])
        return len(pods)

    def health(self) -> bool:
        """Check if Dgraph is reachable."""
        try:
            req = urllib.request.Request(f"{self.url}/health", method="GET")
            with urllib.request.urlopen(req, timeout=2) as resp:
                return resp.status == 200
        except (urllib.error.URLError, OSError):
            return False

    def clear_cache(self) -> None:
        """Clear the blast radius cache."""
        self._cache.clear()


# Singleton instance (lazy-initialized)
_kg_client: Optional[KnowledgeGraphClient] = None


def get_kg_client() -> Optional[KnowledgeGraphClient]:
    """Get the singleton KnowledgeGraphClient instance.

    Returns None if DGRAPH_URL is not set or Dgraph is unreachable on init.
    The client still works — it returns 0 for all queries when Dgraph is down.
    """
    global _kg_client
    if _kg_client is None:
        _kg_client = KnowledgeGraphClient()
    return _kg_client
