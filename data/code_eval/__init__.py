"""Evaluation instrumentation for FlowMap.

The package deliberately contains no pipeline policy: callers decide which
stages to measure and pass their resulting graphs/documents to the collectors.
"""

from .models import CodebaseStats, GraphStats, LLMCallRecord, RunRecord, StageRecord
from .recorder import EvaluationRecorder
from .stats import collect_codebase_stats, collect_graph_stats

__all__ = [
    "CodebaseStats",
    "EvaluationRecorder",
    "GraphStats",
    "LLMCallRecord",
    "RunRecord",
    "StageRecord",
    "collect_codebase_stats",
    "collect_graph_stats",
]
