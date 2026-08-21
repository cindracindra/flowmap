"""Stage 6: recheck call sites whose retained callee fell to one phase."""

from __future__ import annotations

from domain.phase_relationship import (
    build_phase_signature,
    evaluate_phase_cohesion,
    evaluate_relationship,
)
from domain.phase_segmentation import (
    Analysis,
    Gate,
    callee_count,
    materialise,
)
from domain.phase_structure import BranchStructure, LinearStructure, Structure
from model import Phase


def _structure_node_ids(structures: tuple[Structure, ...]):
    for structure in structures:
        if isinstance(structure, LinearStructure):
            yield from structure.nodeIds
        elif isinstance(structure, BranchStructure):
            for arm in structure.arms:
                yield from _structure_node_ids(arm)


def _phase_for_node(phases: list[Phase], node_id: str) -> Phase | None:
    return next((phase for phase in phases if node_id in phase.nodes), None)


def _adjacency_gate(
    analysis: Analysis,
    phase: Phase,
    frontier_id: str,
    candidate_id: str,
) -> Gate:
    local = evaluate_relationship(analysis.graph, frontier_id, candidate_id)
    cohesion = evaluate_phase_cohesion(
        analysis.graph,
        build_phase_signature(analysis.graph, phase.nodes),
        candidate_id,
    )
    return Gate(frontier_id, candidate_id, "adjacency", local, cohesion)


def _recheck_method(analysis: Analysis, entry_id: str) -> int:
    method = analysis.methods.get(entry_id)
    structure = analysis.structures.get(entry_id)
    if method is None or structure is None or not method.retainedCallIds:
        return 0

    node_order = tuple(_structure_node_ids(structure.structures))
    lapsed = [
        call_site_id
        for call_site_id in node_order
        if call_site_id in method.retainedCallIds
        and callee_count(analysis, call_site_id) == 1
    ]
    updated = 0

    for call_site_id in lapsed:
        left_index = next(
            (
                index
                for index, gate in enumerate(method.gates)
                if gate.kind == "adjacency"
                and gate.candidateId == call_site_id
            ),
            None,
        )
        if left_index is not None:
            left_gate = method.gates[left_index]
            if left_gate.frontierId not in method.retainedCallIds:
                left_phase = _phase_for_node(
                    method.phases, left_gate.frontierId
                )
                if left_phase is not None:
                    method.gates[left_index] = _adjacency_gate(
                        analysis,
                        left_phase,
                        left_gate.frontierId,
                        call_site_id,
                    )

        # The call site must become an ordinary member before its right-side
        # phase signature can be evaluated.
        method.retainedCallIds.remove(call_site_id)
        materialise(analysis, entry_id)

        right_index = next(
            (
                index
                for index, gate in enumerate(method.gates)
                if gate.kind == "adjacency"
                and gate.frontierId == call_site_id
            ),
            None,
        )
        if right_index is not None:
            right_gate = method.gates[right_index]
            if right_gate.candidateId not in method.retainedCallIds:
                current_phase = _phase_for_node(method.phases, call_site_id)
                if current_phase is not None:
                    method.gates[right_index] = _adjacency_gate(
                        analysis,
                        current_phase,
                        call_site_id,
                        right_gate.candidateId,
                    )
                    materialise(analysis, entry_id)

        updated += 1
    return updated


def recheck_lapsed_retentions(analysis: Analysis) -> int:
    """Recheck lapsed retained calls in callee-before-caller DFS order."""
    analysis.clear_counts()
    visited: set[str] = set()
    visiting: set[str] = set()
    updated = 0

    def visit(entry_id: str) -> None:
        nonlocal updated
        if entry_id in visited or entry_id not in analysis.structures:
            return
        if entry_id in visiting:
            return

        visiting.add(entry_id)
        structure = analysis.structures[entry_id]
        for call_site_id in _structure_node_ids(structure.structures):
            for callee_entry_id in analysis.calleeEntries.get(call_site_id, ()):
                visit(callee_entry_id)
        visiting.remove(entry_id)

        updated += _recheck_method(analysis, entry_id)
        analysis.clear_counts()
        visited.add(entry_id)

    for root_id in analysis.graph.roots:
        visit(root_id)
    for entry_id in analysis.structures:
        visit(entry_id)
    return updated
