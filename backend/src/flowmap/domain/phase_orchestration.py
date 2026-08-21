"""Run phase discovery once, then project it onto flattened traces."""

from __future__ import annotations

from domain.phase_exclusion import find_excluded_operations
from domain.phase_labelling import PhaseLabeler, label_phases
from domain.phase_overlay import overlay_phases, phase_tree_dict
from domain.phase_resolution import BatchGateResolver, resolve_uncertain_gates
from domain.phase_retention import recheck_lapsed_retentions
from domain.phase_segmentation import Analysis, analyse
from model import Graph


def analyse_codebase_phases(
    filtered_graph: Graph,
    gate_resolver: BatchGateResolver | None = None,
) -> Analysis:
    """Run Stages 1–6 once on the filtered whole-codebase graph."""
    excluded = find_excluded_operations(filtered_graph)
    analysis = analyse(filtered_graph, excluded)
    if gate_resolver is not None:
        resolve_uncertain_gates(analysis, gate_resolver)
    recheck_lapsed_retentions(analysis)
    return analysis


def discover_phases(
    analysis: Analysis,
    flattened_graph: Graph,
    labeler: PhaseLabeler | None = None,
) -> dict:
    """Run Stages 7 and 9 for one flattened operation trace."""
    phases = overlay_phases(analysis, flattened_graph)
    if labeler is not None:
        label_phases(flattened_graph, phases, labeler)
    return phase_tree_dict(flattened_graph, phases)
