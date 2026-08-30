"""Run method-level phase discovery once for the filtered codebase graph."""

from __future__ import annotations

from domain.phase_exclusion import find_excluded_operations
from domain.phase_resolution import BatchGateResolver, resolve_uncertain_gates
from domain.phase_retention import recheck_lapsed_retentions
from domain.phase_segmentation import Analysis, analyse
from model import Graph, MethodDefinition


def analyse_codebase_phases(
    filtered_graph: Graph,
    gate_resolver: BatchGateResolver | None = None,
    method_definitions: dict[str, MethodDefinition] | None = None,
) -> Analysis:
    """Run Stages 1–6 once on the filtered whole-codebase graph."""
    excluded = find_excluded_operations(filtered_graph)
    analysis = analyse(filtered_graph, excluded, method_definitions)
    if gate_resolver is not None:
        resolve_uncertain_gates(analysis, gate_resolver)
    recheck_lapsed_retentions(analysis)
    return analysis
