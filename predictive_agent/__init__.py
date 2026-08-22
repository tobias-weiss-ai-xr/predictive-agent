"""openDesk Predictive Agent v4.0 — Predictive Kubernetes Health Monitor.

Performance Optimization Modules:
- l1_cache: L1 caching layer for pod states, predictions, and LLM prompts
- llm_batch: Batch processing and parallel execution for LLM calls
- optimize: Unified performance optimization interface
"""
__version__ = "4.0.0"

from predictive_agent.l1_cache import get_cache, L1Cache
from predictive_agent.llm_batch import LLMBatchProcessor, ParallelLLMProcessor
from predictive_agent.optimize import PerformanceOptimizer, get_optimizer, configure_optimizations

__all__ = [
    'get_cache', 'L1Cache',
    'LLMBatchProcessor', 'ParallelLLMProcessor',
    'PerformanceOptimizer', 'get_optimizer', 'configure_optimizations',
]
