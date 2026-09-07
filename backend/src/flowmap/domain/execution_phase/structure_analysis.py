"""Recursive first-pass analysis of linear and branching structures."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TypeAlias

from domain.execution_phase.structure import BranchStructure, LinearStructure, Structure
from model import MethodDefinition, NodeSemanticFeatures, Phase, UnresolvedGate
from .cohesion import evaluate_op_to_op, evaluate_region_to_op, evaluate_region_to_region
from .semantic import SemanticSignature, build_core_signature


@dataclass(slots=True)
class StraightStructureAnalysis:
    """Materialised result for one maximal straight-line structure."""

    structure: LinearStructure
    phases: list[Phase] = field(default_factory=list)
    unresolved_gates: list[UnresolvedGate] = field(default_factory=list)
    retained_call_ids: list[str] = field(default_factory=list)

    @property
    def entry_frontier_ids(self) -> tuple[str, ...]:
        """Return the first node, including a retained-call boundary."""
        return self.structure.node_ids[:1]

    @property
    def exit_frontier_ids(self) -> tuple[str, ...]:
        """Return the last node, including a retained-call boundary."""
        return self.structure.node_ids[-1:]


@dataclass(frozen=True, slots=True)
class BranchStructureAnalysis:
    """Analysed alternatives plus flattened views of their content.

    ``arms`` remains the authoritative structural representation. The other
    fields are convenient aggregates for method-level collection and must be
    derived from those arm results.
    """

    structure: BranchStructure
    arms: tuple[tuple[StructureAnalysis, ...], ...]
    phases: tuple[Phase, ...] = ()
    unresolved_gates: tuple[UnresolvedGate, ...] = ()
    retained_call_ids: tuple[str, ...] = ()

    @property
    def entry_frontier_ids(self) -> tuple[str, ...]:
        """Return the first exposed node IDs from every non-empty arm."""
        entries: list[str] = []
        for arm in self.arms:
            for child in arm:
                if child.entry_frontier_ids:
                    entries.extend(child.entry_frontier_ids)
                    break
        return tuple(entries)

    @property
    def exit_frontier_ids(self) -> tuple[str, ...]:
        """Return the last exposed node IDs from every non-empty arm."""
        exits: list[str] = []
        for arm in self.arms:
            for child in reversed(arm):
                if child.exit_frontier_ids:
                    exits.extend(child.exit_frontier_ids)
                    break
        return tuple(exits)


StructureAnalysis: TypeAlias = StraightStructureAnalysis | BranchStructureAnalysis


@dataclass(slots=True)
class StructureBoundaryContext:
    """Shared mutable state for boundary analysis within one method."""

    method: MethodDefinition
    retained_call_ids: frozenset[str]
    direct_flow_pairs: frozenset[tuple[str, str]]
    phase_by_node_id: dict[str, Phase]
    unresolved_gates: list[UnresolvedGate]

    def phase_signature(self, phase: Phase) -> SemanticSignature:
        return build_core_signature(
            SemanticSignature.from_operation(self.method.semanticFeatures[node_id])
            for node_id in phase.nodes
            if node_id in self.method.semanticFeatures
        )

    def merge_phases(self, left: Phase, right: Phase) -> None:
        if left is right:
            return
        for node_id in right.nodes:
            if node_id not in left.nodes:
                left.nodes.append(node_id)
            self.phase_by_node_id[node_id] = left

    def canonical_phases(self, phases: list[Phase]) -> tuple[Phase, ...]:
        """Return each merged phase once, preserving first-seen order."""
        canonical: list[Phase] = []
        seen: set[int] = set()
        for phase in phases:
            if not phase.nodes:
                continue
            merged = self.phase_by_node_id.get(phase.nodes[0], phase)
            identity = id(merged)
            if identity not in seen:
                seen.add(identity)
                canonical.append(merged)
        return tuple(canonical)


def analyse_structure_boundary(
    left: StructureAnalysis,
    right: StructureAnalysis,
    context: StructureBoundaryContext,
) -> None:
    """Evaluate every exposed path between two sibling structures."""
    for left_id in left.exit_frontier_ids:
        for right_id in right.entry_frontier_ids:
            if (
                left_id in context.retained_call_ids
                or right_id in context.retained_call_ids
            ):
                continue

            left_phase = context.phase_by_node_id.get(left_id)
            right_phase = context.phase_by_node_id.get(right_id)
            if left_phase is None or right_phase is None or left_phase is right_phase:
                continue

            has_direct_flow = (left_id, right_id) in context.direct_flow_pairs
            left_signature = context.phase_signature(left_phase)
            right_signature = context.phase_signature(right_phase)
            left_frontier_signature = SemanticSignature.from_operation(
                context.method.semanticFeatures.get(
                    left_id, NodeSemanticFeatures()
                )
            )
            right_frontier_signature = SemanticSignature.from_operation(
                context.method.semanticFeatures.get(
                    right_id, NodeSemanticFeatures()
                )
            )
            has_directional_write_read = bool(
                left_frontier_signature.fields_written
                & right_frontier_signature.fields_read
            )
            if has_direct_flow or has_directional_write_read:
                # Structures expose the boundary; the compared units are the
                # phases containing their exit and entry frontiers. Confirmed
                # directional data flow or field write-to-read evidence between
                # those phases is decisive.
                context.merge_phases(left_phase, right_phase)
                continue

            decision = evaluate_region_to_region(
                left_signature,
                right_signature,
            )
            if decision.verdict == "RELATED":
                context.merge_phases(left_phase, right_phase)
            elif decision.verdict == "UNKNOWN":
                context.unresolved_gates.append(
                    UnresolvedGate(
                        id=f"region:{left_id}-{right_id}",
                        left_id=left_id,
                        right_id=right_id,
                        confidence=decision.confidence,
                        evidence=decision.evidence + decision.missing_evidence,
                        kind="structure-boundary",
                    )
                )


def analyse_sibling_structures(
    siblings: tuple[StructureAnalysis, ...],
    context: StructureBoundaryContext,
) -> None:
    """Recursively analyse boundaries in branch arms and between siblings."""
    for sibling in siblings:
        if isinstance(sibling, BranchStructureAnalysis):
            for arm in sibling.arms:
                analyse_sibling_structures(arm, context)

    for left, right in zip(siblings, siblings[1:]):
        analyse_structure_boundary(left, right, context)


def analyse_straight_structure(
    method: MethodDefinition,
    structure: LinearStructure,
    retained_call_ids: frozenset[str],
    direct_flow_pairs: frozenset[tuple[str, str]],
) -> StraightStructureAnalysis:
    """Segment one straight-line structure into initial phases."""

    phases: list[Phase] = []
    unresolved_gates: list[UnresolvedGate] = []
    encountered_retained_call_ids: list[str] = []
    frontier_id: str | None = None
    active_phase: Phase | None = None

    def signature(node_id: str) -> SemanticSignature:
        features = method.semanticFeatures.get(node_id, NodeSemanticFeatures())
        return SemanticSignature.from_operation(features)

    def close_active_phase() -> None:
        nonlocal active_phase
        if active_phase is not None:
            phases.append(active_phase)
            active_phase = None

    for call_id in structure.node_ids:
        if call_id in retained_call_ids:
            close_active_phase()
            encountered_retained_call_ids.append(call_id)
            frontier_id = None
            continue

        if active_phase is None:
            active_phase = Phase(nodes=[call_id])
            frontier_id = call_id
            continue

        assert frontier_id is not None
        left_signature = signature(frontier_id)
        right_signature = signature(call_id)
        local = evaluate_op_to_op(
            left_signature,
            right_signature,
            direct_flow=(frontier_id, call_id) in direct_flow_pairs,
        )

        unresolved_evidence: tuple[str, ...] | None = None
        unresolved_confidence = local.confidence
        merge = False

        if local.verdict == "RELATED":
            if {"direct-data-flow", "directional-write-read"} & set(local.evidence):
                merge = True
            else:
                region_signature = build_core_signature(
                    signature(node_id) for node_id in active_phase.nodes
                )
                region = evaluate_region_to_op(region_signature, right_signature)
                if region.verdict == "UNRELATED":
                    unresolved_evidence = local.evidence + region.evidence
                    unresolved_confidence = max(local.confidence, region.confidence)
                else:
                    merge = True
        elif local.verdict == "UNKNOWN":
            unresolved_evidence = local.evidence + local.missing_evidence

        if merge:
            active_phase.nodes.append(call_id)
        else:
            close_active_phase()
            active_phase = Phase(nodes=[call_id])
            if unresolved_evidence is not None:
                unresolved_gates.append(
                    UnresolvedGate(
                        id=f"{frontier_id}-{call_id}",
                        left_id=frontier_id,
                        right_id=call_id,
                        confidence=unresolved_confidence,
                        evidence=unresolved_evidence,
                    )
                )

        frontier_id = call_id

    close_active_phase()
    return StraightStructureAnalysis(
        structure=structure,
        phases=phases,
        unresolved_gates=unresolved_gates,
        retained_call_ids=encountered_retained_call_ids,
    )


def analyse_branch_structure(
    method: MethodDefinition,
    structure: BranchStructure,
    retained_call_ids: frozenset[str],
    direct_flow_pairs: frozenset[tuple[str, str]],
) -> BranchStructureAnalysis:
    """Recursively analyse each child structure in each independent arm."""

    def analyse_child(child: Structure) -> StructureAnalysis:
        if isinstance(child, LinearStructure):
            return analyse_straight_structure(
                method,
                child,
                retained_call_ids,
                direct_flow_pairs,
            )
        return analyse_branch_structure(
            method,
            child,
            retained_call_ids,
            direct_flow_pairs,
        )

    arms: list[tuple[StructureAnalysis, ...]] = []
    phases: list[Phase] = []
    unresolved_gates: list[UnresolvedGate] = []
    encountered_retained_call_ids: list[str] = []

    for arm in structure.arms:
        analysed_arm: list[StructureAnalysis] = []
        for child in arm:
            result = analyse_child(child)
            analysed_arm.append(result)
            phases.extend(result.phases)
            unresolved_gates.extend(result.unresolved_gates)
            encountered_retained_call_ids.extend(result.retained_call_ids)
        arms.append(tuple(analysed_arm))

    return BranchStructureAnalysis(
        structure=structure,
        arms=tuple(arms),
        phases=tuple(phases),
        unresolved_gates=tuple(unresolved_gates),
        retained_call_ids=tuple(encountered_retained_call_ids),
    )
