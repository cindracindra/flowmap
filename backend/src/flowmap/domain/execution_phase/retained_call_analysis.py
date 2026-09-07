"""Post-resolution restoration of calls whose callees collapse to one phase."""

from __future__ import annotations

from dataclasses import replace
from typing import Iterable

from domain.execution_phase.cohesion import evaluate_op_to_op, evaluate_region_to_op
from domain.execution_phase.method_analysis import (
    MethodAnalysis,
    call_effective_phase_count,
)
from domain.execution_phase.semantic import SemanticSignature, build_core_signature
from domain.execution_phase.structure_analysis import (
    BranchStructureAnalysis,
    StraightStructureAnalysis,
    StructureAnalysis,
)
from model import MethodDefinition, NodeSemanticFeatures, Phase


def _nearest_exit_frontiers(
    siblings: tuple[StructureAnalysis, ...],
    before: int,
) -> tuple[str, ...]:
    for sibling in reversed(siblings[:before]):
        if sibling.exit_frontier_ids:
            return sibling.exit_frontier_ids
    return ()


def _nearest_entry_frontiers(
    siblings: tuple[StructureAnalysis, ...],
    after: int,
) -> tuple[str, ...]:
    for sibling in siblings[after:]:
        if sibling.entry_frontier_ids:
            return sibling.entry_frontier_ids
    return ()


def _find_retained_call_frontiers(
    siblings: tuple[StructureAnalysis, ...],
    retained_call_id: str,
) -> tuple[tuple[str, ...], tuple[str, ...]] | None:
    for index, sibling in enumerate(siblings):
        if isinstance(sibling, StraightStructureAnalysis):
            node_ids = sibling.structure.node_ids
            if retained_call_id not in node_ids:
                continue
            call_index = node_ids.index(retained_call_id)
            left = node_ids[call_index - 1 : call_index] if call_index else ()
            right = node_ids[call_index + 1 : call_index + 2]
        elif isinstance(sibling, BranchStructureAnalysis):
            found = next(
                (
                    frontiers
                    for arm in sibling.arms
                    if (
                        frontiers := _find_retained_call_frontiers(
                            arm, retained_call_id
                        )
                    )
                    is not None
                ),
                None,
            )
            if found is None:
                continue
            left, right = found
        else:
            continue

        if not left:
            left = _nearest_exit_frontiers(siblings, index)
        if not right:
            right = _nearest_entry_frontiers(siblings, index + 1)
        return left, right
    return None


def retained_call_frontiers(
    structure_analyses: tuple[StructureAnalysis, ...],
    retained_call_id: str,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return immediate left and right frontier IDs for one retained call."""
    return _find_retained_call_frontiers(
        structure_analyses, retained_call_id
    ) or ((), ())


def _phase_containing(analysis: MethodAnalysis, node_id: str) -> Phase | None:
    return next((phase for phase in analysis.phases if node_id in phase.nodes), None)


def _unique_phases(phases: Iterable[Phase]) -> tuple[Phase, ...]:
    unique: list[Phase] = []
    seen: set[int] = set()
    for phase in phases:
        identity = id(phase)
        if identity not in seen:
            seen.add(identity)
            unique.append(phase)
    return tuple(unique)


def phases_for_frontiers(
    analysis: MethodAnalysis,
    frontier_ids: tuple[str, ...],
) -> tuple[Phase, ...]:
    """Resolve ordinary frontier IDs to their current canonical phases."""
    phases: list[Phase] = []
    for frontier_id in frontier_ids:
        if frontier_id in analysis.retained_call_ids:
            continue
        phase = _phase_containing(analysis, frontier_id)
        if phase is not None:
            phases.append(phase)
    return _unique_phases(phases)


def _phase_signature(method: MethodDefinition, phase: Phase) -> SemanticSignature:
    return build_core_signature(
        SemanticSignature.from_operation(
            method.semanticFeatures.get(node_id, NodeSemanticFeatures())
        )
        for node_id in phase.nodes
    )


def _related_to_restored_call(
    method: MethodDefinition,
    phase: Phase,
    frontier_id: str,
    call_id: str,
    direct_flow_pairs: frozenset[tuple[str, str]],
    *,
    call_is_right: bool,
) -> bool:
    if call_is_right:
        left_id, right_id = frontier_id, call_id
    else:
        left_id, right_id = call_id, frontier_id

    left_signature = SemanticSignature.from_operation(
        method.semanticFeatures.get(left_id, NodeSemanticFeatures())
    )
    right_signature = SemanticSignature.from_operation(
        method.semanticFeatures.get(right_id, NodeSemanticFeatures())
    )
    local = evaluate_op_to_op(
        left_signature,
        right_signature,
        direct_flow=(left_id, right_id) in direct_flow_pairs,
    )
    if {"direct-data-flow", "directional-write-read"} & set(local.evidence):
        return True

    call_signature = SemanticSignature.from_operation(
        method.semanticFeatures.get(call_id, NodeSemanticFeatures())
    )
    phase_signature = _phase_signature(method, phase)
    return evaluate_region_to_op(
        phase_signature, call_signature
    ).verdict == "RELATED"


def _related_frontier_phases(
    method: MethodDefinition,
    analysis: MethodAnalysis,
    frontier_ids: tuple[str, ...],
    call_id: str,
    direct_flow_pairs: frozenset[tuple[str, str]],
    *,
    call_is_right: bool,
) -> tuple[Phase, ...]:
    related: list[Phase] = []
    for frontier_id in frontier_ids:
        if frontier_id in analysis.retained_call_ids:
            continue
        phase = _phase_containing(analysis, frontier_id)
        if phase is None:
            continue
        if _related_to_restored_call(
            method,
            phase,
            frontier_id,
            call_id,
            direct_flow_pairs,
            call_is_right=call_is_right,
        ):
            related.append(phase)
    return _unique_phases(related)


def _restore_retained_call(
    method: MethodDefinition,
    analysis: MethodAnalysis,
    call_id: str,
    direct_flow_pairs: frozenset[tuple[str, str]],
) -> MethodAnalysis:
    left_ids, right_ids = retained_call_frontiers(
        analysis.structure_analyses, call_id
    )
    left_phases = _related_frontier_phases(
        method,
        analysis,
        left_ids,
        call_id,
        direct_flow_pairs,
        call_is_right=True,
    )
    right_phases = _related_frontier_phases(
        method,
        analysis,
        right_ids,
        call_id,
        direct_flow_pairs,
        call_is_right=False,
    )
    related_phases = _unique_phases((*left_phases, *right_phases))

    phase_indexes = {id(phase): index for index, phase in enumerate(analysis.phases)}
    all_neighbour_phases = phases_for_frontiers(analysis, (*left_ids, *right_ids))
    placement_phases = related_phases or all_neighbour_phases
    insert_at = min(
        (phase_indexes[id(phase)] for phase in placement_phases),
        default=len(analysis.phases),
    )

    restored_phase = Phase(nodes=list(dict.fromkeys((
        *(node for phase in left_phases for node in phase.nodes),
        call_id,
        *(node for phase in right_phases for node in phase.nodes),
    ))))
    related_ids = {id(phase) for phase in related_phases}
    phases = [phase for phase in analysis.phases if id(phase) not in related_ids]
    phases.insert(insert_at, restored_phase)

    return replace(
        analysis,
        phases=tuple(phases),
        retained_call_ids=analysis.retained_call_ids - {call_id},
        unresolved_gates=tuple(
            gate
            for gate in analysis.unresolved_gates
            if not (
                gate.left_id in restored_phase.nodes
                and gate.right_id in restored_phase.nodes
            )
        ),
    )


def recheck_retained_calls(
    methods_by_entry_id: dict[str, MethodDefinition],
    analyses_by_entry_id: dict[str, MethodAnalysis],
    callee_entries_by_call_id: dict[str, tuple[str, ...]],
    direct_flow_pairs: frozenset[tuple[str, str]],
) -> int:
    """Restore newly single-phase callees until no caller changes remain."""
    restored_count = 0
    changed = True
    bottom_up_entry_ids = tuple(analyses_by_entry_id)
    while changed:
        changed = False
        for entry_id in bottom_up_entry_ids:
            method = methods_by_entry_id.get(entry_id)
            analysis = analyses_by_entry_id.get(entry_id)
            if method is None or analysis is None:
                continue
            for call_id in tuple(analysis.retained_call_ids):
                effective_count = call_effective_phase_count(
                    call_id,
                    analyses_by_entry_id,
                    callee_entries_by_call_id,
                )
                if effective_count is None or effective_count > 1:
                    continue
                analysis = _restore_retained_call(
                    method, analysis, call_id, direct_flow_pairs
                )
                analyses_by_entry_id[entry_id] = analysis
                restored_count += 1
                changed = True
    return restored_count
