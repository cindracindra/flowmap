"""Bottom-up resolution and method-local execution-phase analysis."""

from __future__ import annotations

from dataclasses import dataclass

from domain.execution_phase.structure import (
    BranchStructure,
    LinearStructure,
    MethodStructure,
    Structure,
)
from model import MethodDefinition, Phase, UnresolvedGate
from .structure_analysis import (
    StructureBoundaryContext,
    StructureAnalysis,
    analyse_branch_structure,
    analyse_sibling_structures,
    analyse_straight_structure,
)


@dataclass(frozen=True, slots=True)
class MethodAnalysis:
    """Intermediate first-pass result for one reusable method definition."""

    method_entry_id: str
    phases: tuple[Phase, ...] = ()
    retained_call_ids: frozenset[str] = frozenset()
    unresolved_gates: tuple[UnresolvedGate, ...] = ()
    structure_analyses: tuple[StructureAnalysis, ...] = ()


def structure_call_ids(structures: tuple[Structure, ...]):
    """Yield call IDs in deterministic structural order."""
    for structure in structures:
        if isinstance(structure, LinearStructure):
            yield from structure.node_ids
            continue
        for arm in structure.arms:
            yield from structure_call_ids(arm)


def analyse_method(
    method: MethodDefinition,
    structure: MethodStructure,
    retained_call_ids: frozenset[str],
    direct_flow_pairs: frozenset[tuple[str, str]],
) -> MethodAnalysis:
    """Run the first systematic phase analysis for one resolved method.
    
    Unknown systematic decisions must remain boundaries for the first pass.
    """
    phases: list[Phase] = []
    unresolved_gates: list[UnresolvedGate] = []
    encountered_retained_call_ids: set[str] = set()
    structure_analyses: list[StructureAnalysis] = []

    for struct in structure.structures:
        if isinstance(struct, LinearStructure):
            result = analyse_straight_structure(
                method,
                struct,
                retained_call_ids,
                direct_flow_pairs,
            )
            structure_analyses.append(result)
            phases.extend(result.phases)
            unresolved_gates.extend(result.unresolved_gates)
            encountered_retained_call_ids.update(result.retained_call_ids)
        elif isinstance(struct, BranchStructure):
            result = analyse_branch_structure(
                method,
                struct,
                retained_call_ids,
                direct_flow_pairs,
            )
            structure_analyses.append(result)
            phases.extend(result.phases)
            unresolved_gates.extend(result.unresolved_gates)
            encountered_retained_call_ids.update(result.retained_call_ids)
    
    phase_by_node_id = {
        node_id: phase
        for phase in phases
        for node_id in phase.nodes
    }

    boundary_context = StructureBoundaryContext(
        method=method,
        retained_call_ids=retained_call_ids,
        direct_flow_pairs=direct_flow_pairs,
        phase_by_node_id=phase_by_node_id,
        unresolved_gates=unresolved_gates,
    )
    analyse_sibling_structures(tuple(structure_analyses), boundary_context)
    
    return MethodAnalysis(
        method_entry_id=method.entryId,
        phases=boundary_context.canonical_phases(phases),
        retained_call_ids=frozenset(encountered_retained_call_ids),
        unresolved_gates=tuple(unresolved_gates),
        structure_analyses=tuple(structure_analyses),
    )


def effective_phase_count(
    entry_id: str,
    analyses_by_entry_id: dict[str, MethodAnalysis],
    callee_entries_by_call_id: dict[str, tuple[str, ...]],
    _counting: set[str] | None = None,
) -> int | None:
    """Return a resolved method's local plus retained effective phase count.

    ``None`` means the method is not resolved or a recursive count is currently
    indeterminate. A retained polymorphic call contributes the maximum count
    among its possible resolved targets.
    """
    analysis = analyses_by_entry_id.get(entry_id)
    if analysis is None:
        return None

    counting = _counting if _counting is not None else set()
    if entry_id in counting:
        return None
    counting.add(entry_id)
    try:
        retained_count = 0
        for call_id in analysis.retained_call_ids:
            target_counts = [
                count
                for target_entry_id in callee_entries_by_call_id.get(call_id, ())
                if (
                    count := effective_phase_count(
                        target_entry_id,
                        analyses_by_entry_id,
                        callee_entries_by_call_id,
                        counting,
                    )
                )
                is not None
            ]
            if target_counts:
                retained_count += max(target_counts)
        return len(analysis.phases) + retained_count
    finally:
        counting.remove(entry_id)


def call_effective_phase_count(
    call_id: str,
    analyses_by_entry_id: dict[str, MethodAnalysis],
    callee_entries_by_call_id: dict[str, tuple[str, ...]],
) -> int | None:
    targets = callee_entries_by_call_id.get(call_id, ())
    if not targets:
        return 0
    counts = [
        effective_phase_count(
            target_entry_id,
            analyses_by_entry_id,
            callee_entries_by_call_id,
        )
        for target_entry_id in targets
    ]
    if any(count is None for count in counts):
        return None
    return max((count for count in counts if count is not None), default=0)


def resolve(
    entry_id: str,
    *,
    methods_by_entry_id: dict[str, MethodDefinition],
    structures_by_entry_id: dict[str, MethodStructure],
    callee_entries_by_call_id: dict[str, tuple[str, ...]],
    direct_flow_pairs: frozenset[tuple[str, str]],
    analyses_by_entry_id: dict[str, MethodAnalysis],
    resolving_entry_ids: set[str],
) -> MethodAnalysis | None:
    """Resolve one method after its non-recursive internal callees.

    Results are cached in ``analyses_by_entry_id``. A target encountered again
    on the active stack is recursive and remains conservatively unresolved for
    this first pass; its call site is therefore retained.
    """
    if entry_id in analyses_by_entry_id:
        return analyses_by_entry_id[entry_id]
    if entry_id in resolving_entry_ids:
        return None

    method = methods_by_entry_id.get(entry_id)
    structure = structures_by_entry_id.get(entry_id)
    if method is None or structure is None:
        return None

    resolving_entry_ids.add(entry_id)
    try:
        call_ids = tuple(structure_call_ids(structure.structures))
        for call_id in call_ids:
            for callee_entry_id in callee_entries_by_call_id.get(call_id, ()):
                resolve(
                    callee_entry_id,
                    methods_by_entry_id=methods_by_entry_id,
                    structures_by_entry_id=structures_by_entry_id,
                    callee_entries_by_call_id=callee_entries_by_call_id,
                    direct_flow_pairs=direct_flow_pairs,
                    analyses_by_entry_id=analyses_by_entry_id,
                    resolving_entry_ids=resolving_entry_ids,
                )

        retained_call_ids = frozenset(
            call_id
            for call_id in call_ids
            if (
                (count := call_effective_phase_count(
                    call_id,
                    analyses_by_entry_id,
                    callee_entries_by_call_id,
                ))
                is None
                or count > 1
            )
        )
        result = analyse_method(
            method,
            structure,
            retained_call_ids,
            direct_flow_pairs,
        )
        if result.method_entry_id != entry_id:
            raise ValueError(
                f"analysis for {entry_id!r} returned result for "
                f"{result.method_entry_id!r}"
            )
        analyses_by_entry_id[entry_id] = result
        return result
    finally:
        resolving_entry_ids.remove(entry_id)
