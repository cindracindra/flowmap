"""Prepare whole-codebase inputs for execution-phase analysis.

General CFG filtering and MethodDefinition construction belong to the caller.
This module begins at the phase boundary: exclusion, phase-specific structural
projection, and the lightweight interprocedural index needed for retained
calls.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from domain.execution_phase.exclusion import ExclusionReason, find_excluded_operations
from domain.execution_phase.method_analysis import MethodAnalysis, resolve
from domain.execution_phase.retained_call_analysis import recheck_retained_calls
from domain.execution_phase.resolution import (
    BatchGateResolver,
    UnresolvedGateQuestion,
    build_gate_questions,
    resolve_uncertain_gates,
)
from domain.execution_phase.structure import MethodStructure, build_method_structures
from model import Graph, MethodDefinition


@dataclass(frozen=True, slots=True)
class ExecutionPhaseAnalysis:
    """Complete phase-specific whole-codebase analysis state."""

    graph: Graph
    excluded: dict[str, ExclusionReason]
    structures_by_entry_id: dict[str, MethodStructure]
    callee_entries_by_call_id: dict[str, tuple[str, ...]]
    direct_flow_pairs: frozenset[tuple[str, str]]
    analyses_by_entry_id: dict[str, MethodAnalysis]
    unresolved_gate_questions: tuple[UnresolvedGateQuestion, ...]


def build_callee_index(
    methods_by_entry_id: dict[str, MethodDefinition],
) -> dict[str, tuple[str, ...]]:
    """Index each internal call site by all method entries it may invoke.

    Multiple targets are retained for polymorphic dispatch. External targets
    are absent from ``methods_by_entry_id`` and therefore do not participate in
    bottom-up method resolution.
    """
    targets_by_call_id: dict[str, list[str]] = defaultdict(list)
    internal_entry_ids = set(methods_by_entry_id)

    for method in methods_by_entry_id.values():
        for edge in method.invokeEdges:
            if edge.target in internal_entry_ids:
                targets_by_call_id[edge.source].append(edge.target)

    return {
        call_id: tuple(sorted(set(target_entry_ids)))
        for call_id, target_entry_ids in targets_by_call_id.items()
    }


def build_direct_flow_index(graph: Graph) -> frozenset[tuple[str, str]]:
    """Return the directional call-to-call DDG relationships Joern confirmed."""
    call_ids = {node.id for node in graph.nodes if node.type == "call"}
    return frozenset(
        (edge.source, edge.target)
        for edge in graph.edges
        if edge.type == "data"
        and edge.source in call_ids
        and edge.target in call_ids
    )


def execution_phase_analysis(
    filtered_graph: Graph,
    methods_by_entry_id: dict[str, MethodDefinition],
    gate_resolver: BatchGateResolver | None = None,
) -> ExecutionPhaseAnalysis:
    """Build and systematically resolve phase-specific codebase structures.

    ``gate_resolver`` is reserved for the later uncertainty-resolution stage
    and is intentionally not invoked yet.
    """
    excluded = find_excluded_operations(filtered_graph)
    structures = build_method_structures(
        methods_by_entry_id,
        excluded,
    )
    callee_index = build_callee_index(methods_by_entry_id)
    direct_flow_pairs = build_direct_flow_index(filtered_graph)

    analyses_by_entry_id: dict[str, MethodAnalysis] = {}
    resolving_entry_ids: set[str] = set()
    for entry_id in methods_by_entry_id:
        if entry_id not in structures:
            continue
        resolve(
            entry_id,
            methods_by_entry_id=methods_by_entry_id,
            structures_by_entry_id=structures,
            callee_entries_by_call_id=callee_index,
            direct_flow_pairs=direct_flow_pairs,
            analyses_by_entry_id=analyses_by_entry_id,
            resolving_entry_ids=resolving_entry_ids,
        )
    
    unresolved_gate_questions = build_gate_questions(
        methods_by_entry_id,
        analyses_by_entry_id,
        direct_flow_pairs,
    )

    if gate_resolver is not None:
        resolve_uncertain_gates(
            methods_by_entry_id,
            analyses_by_entry_id,
            direct_flow_pairs,
            gate_resolver,
        )
        recheck_retained_calls(
            methods_by_entry_id,
            analyses_by_entry_id,
            callee_index,
            direct_flow_pairs,
        )
        unresolved_gate_questions = build_gate_questions(
            methods_by_entry_id,
            analyses_by_entry_id,
            direct_flow_pairs,
        )

    return ExecutionPhaseAnalysis(
        graph=filtered_graph,
        excluded=excluded,
        structures_by_entry_id=structures,
        callee_entries_by_call_id=callee_index,
        direct_flow_pairs=direct_flow_pairs,
        analyses_by_entry_id=analyses_by_entry_id,
        unresolved_gate_questions=unresolved_gate_questions,
    )
