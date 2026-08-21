"""Stages 3 and 4: analyse every method, callees before callers."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field, replace
from typing import Callable, Literal

from domain.phase_exclusion import ExclusionReason
from domain.phase_relationship import (
    CandidateAction,
    CohesionDecision,
    RelationshipDecision,
    build_phase_signature,
    evaluate_phase_cohesion,
    evaluate_region_cohesion,
    evaluate_relationship,
)
from domain.phase_structure import (
    BranchStructure,
    LinearStructure,
    MethodStructure,
    Structure,
    build_method_structures,
)
from model import Graph, Phase


GateKind = Literal[
    "adjacency",
    "branch-entry",
    "branch-convergence",
    "arm-alternation",
    "region-split",
]

DecisionSource = Literal["systematic", "llm", "region", "fallback", "structural"]


@dataclass(frozen=True, slots=True)
class GateOverride:
    action: Literal["MERGE", "SPLIT"]
    decidedBy: Literal["llm", "region"]
    confidence: float
    evidence: tuple[str, ...] = ()


def systematic_action(
    local: RelationshipDecision | None,
    cohesion: CohesionDecision | None,
) -> CandidateAction:
    """Apply the local-first Stage 4 decision table."""
    if local is None or cohesion is None:
        return "SPLIT"
    if local.verdict == "UNRELATED":
        return "SPLIT"
    if local.verdict == "RELATED" and cohesion.verdict != "INCOMPATIBLE":
        return "MERGE"
    return "UNCERTAIN"


@dataclass(frozen=True, slots=True)
class Gate:
    frontierId: str
    candidateId: str
    kind: GateKind = "adjacency"
    local: RelationshipDecision | None = None
    cohesion: CohesionDecision | None = None
    override: GateOverride | None = None

    @property
    def action(self) -> CandidateAction:
        if self.override is not None:
            return self.override.action
        if self.kind != "adjacency":
            return "SPLIT"
        return systematic_action(self.local, self.cohesion)

    @property
    def decidedBy(self) -> DecisionSource:
        if self.override is not None:
            return self.override.decidedBy
        if self.kind != "adjacency":
            return "structural"
        return "fallback" if self.action == "UNCERTAIN" else "systematic"


@dataclass(slots=True)
class MethodAnalysis:
    """Stages 3–6 result for one method.

    This replaces the flat ``MethodSegments`` concept. The Stage 2
    ``MethodStructure`` remains separately stored on ``Analysis``.
    """

    methodEntryId: str
    gates: list[Gate] = field(default_factory=list)
    phases: list[Phase] = field(default_factory=list)
    retainedCallIds: set[str] = field(default_factory=set)


@dataclass(slots=True)
class Analysis:
    graph: Graph
    excluded: dict[str, ExclusionReason]
    structures: dict[str, MethodStructure]
    methods: dict[str, MethodAnalysis]
    calleeEntries: dict[str, tuple[str, ...]] = field(default_factory=dict)
    resolver: Callable[[str], MethodAnalysis | None] | None = field(
        default=None, repr=False
    )
    counts: dict[str, int] = field(default_factory=dict, repr=False)
    resolvingMethods: set[str] = field(default_factory=set, repr=False)
    countingMethods: set[str] = field(default_factory=set, repr=False)

    def clear_counts(self) -> None:
        self.counts.clear()


def _callee_entries(graph: Graph) -> dict[str, tuple[str, ...]]:
    nodes = {node.id: node for node in graph.nodes}
    found: dict[str, list[str]] = defaultdict(list)
    for edge in graph.edges:
        target = nodes.get(edge.target)
        if edge.type == "invoke" and target is not None and target.type == "entry":
            found[edge.source].append(edge.target)
    return {
        call_id: tuple(sorted(dict.fromkeys(entries)))
        for call_id, entries in found.items()
    }


def _structure_node_ids(structures: tuple[Structure, ...]):
    for structure in structures:
        if isinstance(structure, LinearStructure):
            yield from structure.nodeIds
        else:
            for arm in structure.arms:
                yield from _structure_node_ids(arm)


def callee_count(analysis: Analysis, call_site_id: str) -> int:
    """Return the largest phase count among a call site's possible callees."""
    return max(
        (
            phase_count(analysis, entry_id)
            for entry_id in analysis.calleeEntries.get(call_site_id, ())
        ),
        default=0,
    )


def phase_count(analysis: Analysis, entry_id: str) -> int:
    """Count a method's own phases plus phases contributed by retained calls."""
    if entry_id in analysis.countingMethods:
        return 0
    if entry_id not in analysis.methods:
        if analysis.resolver is None:
            return 0
        analysis.resolver(entry_id)
    method = analysis.methods.get(entry_id)
    structure = analysis.structures.get(entry_id)
    if method is None or structure is None:
        return 0

    if entry_id not in analysis.counts:
        analysis.countingMethods.add(entry_id)
        try:
            retained = sum(
                callee_count(analysis, call_site_id)
                for call_site_id in method.retainedCallIds
            )
            analysis.counts[entry_id] = len(method.phases) + retained
        finally:
            analysis.countingMethods.discard(entry_id)
    return analysis.counts[entry_id]


def _phase(nodes: list[str]) -> Phase:
    return Phase(nodes=list(nodes))


def _append_linear(
    analysis: Analysis,
    node_ids: tuple[str, ...],
    phases: list[Phase],
    gates: list[Gate],
    boundary_kind: GateKind | None,
    boundary_frontier: str | None,
    retained_call_ids: set[str],
) -> None:
    previous_token: str | None = None
    current: Phase | None = None

    for node_id in node_ids:
        retained = node_id in retained_call_ids

        if retained:
            if previous_token is not None:
                gates.append(Gate(previous_token, node_id, "adjacency"))
            elif boundary_kind is not None and boundary_frontier is not None:
                gates.append(Gate(boundary_frontier, node_id, boundary_kind))
            previous_token = node_id
            current = None
            continue

        if previous_token is not None:
            if current is None:
                gates.append(Gate(previous_token, node_id, "adjacency"))
                current = _phase([node_id])
                phases.append(current)
            else:
                local = evaluate_relationship(analysis.graph, previous_token, node_id)
                cohesion = evaluate_phase_cohesion(
                    analysis.graph,
                    build_phase_signature(analysis.graph, current.nodes),
                    node_id,
                )
                gate = Gate(previous_token, node_id, "adjacency", local, cohesion)
                gates.append(gate)
                if gate.action == "MERGE":
                    current.nodes.append(node_id)
                else:
                    current = _phase([node_id])
                    phases.append(current)
        else:
            if boundary_kind is not None and boundary_frontier is not None:
                gates.append(Gate(boundary_frontier, node_id, boundary_kind))
            current = _phase([node_id])
            phases.append(current)

        previous_token = node_id


def _segment_structures_first_pass(
    analysis: Analysis,
    structures: tuple[Structure, ...],
    gates: list[Gate],
    retained_call_ids: set[str],
) -> list[Phase]:
    """Build initial phases and gates without making region decisions."""
    phases: list[Phase] = []
    previous_structure: Structure | None = None

    for structure in structures:
        if isinstance(structure, LinearStructure):
            if isinstance(previous_structure, BranchStructure):
                boundary: GateKind | None = None
                boundary_frontier = None
                gates.append(
                    Gate(
                        previous_structure.groupId,
                        structure.nodeIds[0],
                        "branch-convergence",
                    )
                )
            elif previous_structure is not None:
                boundary = "region-split"
                boundary_frontier = previous_structure.nodeIds[-1]
            else:
                boundary = None
                boundary_frontier = None
            _append_linear(
                analysis,
                structure.nodeIds,
                phases,
                gates,
                boundary,
                boundary_frontier,
                retained_call_ids,
            )
            previous_structure = structure
            continue

        if isinstance(previous_structure, LinearStructure):
            gates.append(Gate(
                previous_structure.nodeIds[-1],
                structure.groupId,
                "branch-entry",
            ))
        elif isinstance(previous_structure, BranchStructure):
            gates.append(Gate(
                previous_structure.groupId,
                structure.groupId,
                "branch-entry",
            ))

        arm_phases: list[Phase] = []
        previous_arm_tail: str | None = None
        for arm in structure.arms:
            arm_node_ids = tuple(_structure_node_ids(arm))
            resolved_arm = _segment_structures_first_pass(
                analysis, arm, gates, retained_call_ids
            )
            if previous_arm_tail is not None and arm_node_ids:
                gates.append(
                    Gate(
                        previous_arm_tail,
                        arm_node_ids[0],
                        "arm-alternation",
                    )
                )
            if arm_node_ids:
                previous_arm_tail = arm_node_ids[-1]
                arm_phases.extend(resolved_arm)
        phases.extend(arm_phases)

        previous_structure = structure

    return phases


def _segment_method_first_pass(analysis: Analysis, entry_id: str) -> MethodAnalysis:
    """Stage 4A: segment one resolved method without region overrides."""
    structure = analysis.structures[entry_id]
    retained_call_ids = {
        node_id
        for node_id in _structure_node_ids(structure.structures)
        if callee_count(analysis, node_id) > 1
    }
    gates: list[Gate] = []
    phases = _segment_structures_first_pass(
        analysis, structure.structures, gates, retained_call_ids
    )
    return MethodAnalysis(entry_id, gates, phases, retained_call_ids)


def materialise(analysis: Analysis, entry_id: str) -> list[Phase]:
    """Rebuild one method's phases from its structure and current gates."""
    method = analysis.methods.get(entry_id)
    structure = analysis.structures.get(entry_id)
    if method is None or structure is None:
        return []

    pair_gates = {
        (gate.frontierId, gate.candidateId): gate
        for gate in method.gates
        if gate.kind in {"adjacency", "region-split"}
    }

    def replay(items: tuple[Structure, ...]) -> list[Phase]:
        phases: list[Phase] = []
        previous_token: str | None = None

        for item in items:
            if isinstance(item, LinearStructure):
                for node_id in item.nodeIds:
                    if node_id in method.retainedCallIds:
                        previous_token = node_id
                        continue
                    gate = (
                        pair_gates.get((previous_token, node_id))
                        if previous_token is not None
                        else None
                    )
                    if gate is not None and gate.action == "MERGE" and phases:
                        phases[-1].nodes.append(node_id)
                    else:
                        phases.append(_phase([node_id]))
                    previous_token = node_id
                continue

            branch_phases = [phase for arm in item.arms for phase in replay(arm)]
            phases.extend(branch_phases)
            previous_token = None

        return phases

    phases = replay(structure.structures)

    group_nodes: dict[str, set[str]] = {}

    def index_groups(items: tuple[Structure, ...]) -> set[str]:
        contained: set[str] = set()
        for item in items:
            if isinstance(item, LinearStructure):
                contained.update(item.nodeIds)
                continue
            branch_nodes = {
                node_id for arm in item.arms for node_id in index_groups(arm)
            }
            group_nodes[item.groupId] = branch_nodes
            contained.update(branch_nodes)
        return contained

    index_groups(structure.structures)
    parent = list(range(len(phases)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(indices: list[int]) -> None:
        if len(indices) < 2:
            return
        root = find(indices[0])
        for index in indices[1:]:
            other = find(index)
            if other != root:
                parent[other] = root

    def phase_indices(subject_id: str) -> list[int]:
        node_ids = group_nodes.get(subject_id, {subject_id})
        return [
            index
            for index, phase in enumerate(phases)
            if any(node_id in node_ids for node_id in phase.nodes)
        ]

    for gate in method.gates:
        if (
            gate.kind not in {"branch-entry", "branch-convergence"}
            or gate.action != "MERGE"
        ):
            continue
        union(list(dict.fromkeys(
            (*phase_indices(gate.frontierId), *phase_indices(gate.candidateId))
        )))

    grouped: dict[int, list[int]] = {}
    for index in range(len(phases)):
        grouped.setdefault(find(index), []).append(index)
    method.phases = [
        _phase([
            node_id
            for index in indices
            for node_id in phases[index].nodes
        ])
        for _, indices in sorted(
            grouped.items(), key=lambda item: min(item[1])
        )
    ]
    analysis.clear_counts()
    return method.phases


def _collapse_regions(analysis: Analysis, entry_id: str) -> None:
    """Stage 4B: check both sides of each outermost surviving region.

    A successful collapse is materialised before the other side is checked.
    Nested regions are visited only when their enclosing region remains
    separate. A branch-to-branch boundary belongs to the left branch's right
    check, so it is never reconsidered from the right branch.
    """
    method = analysis.methods[entry_id]
    method_structure = analysis.structures[entry_id]

    def region_ids(branch: BranchStructure) -> set[str]:
        return set(_structure_node_ids((branch,)))

    def phase_for_node(node_id: str) -> Phase | None:
        return next(
            (phase for phase in method.phases if node_id in phase.nodes),
            None,
        )

    def region_phase(branch: BranchStructure) -> Phase | None:
        members = region_ids(branch)
        return next(
            (
                phase
                for phase in method.phases
                if any(node_id in members for node_id in phase.nodes)
            ),
            None,
        )

    def cohesion_nodes(subject: Phase | BranchStructure) -> tuple[str, ...]:
        if isinstance(subject, Phase):
            return tuple(subject.nodes)
        members = region_ids(subject)
        return tuple(
            node_id
            for phase in method.phases
            for node_id in phase.nodes
            if node_id in members
        )

    def was_collapsed(branch: BranchStructure) -> bool:
        return any(
            gate.action == "MERGE"
            and gate.kind in {"branch-entry", "branch-convergence"}
            and branch.groupId in {gate.frontierId, gate.candidateId}
            for gate in method.gates
        )

    def gate_index(
        kind: GateKind, frontier_id: str, candidate_id: str
    ) -> int | None:
        return next(
            (
                index
                for index, gate in enumerate(method.gates)
                if gate.kind == kind
                and gate.frontierId == frontier_id
                and gate.candidateId == candidate_id
            ),
            None,
        )

    def calculate(
        index: int | None,
        left: Phase | BranchStructure,
        right: Phase | BranchStructure,
    ) -> bool:
        if index is None:
            return False
        left_nodes = cohesion_nodes(left)
        right_nodes = cohesion_nodes(right)
        if not left_nodes or not right_nodes:
            return False

        cohesion = evaluate_region_cohesion(
            analysis.graph, left_nodes, right_nodes
        )
        override = (
            GateOverride(
                "MERGE", "region", cohesion.confidence, cohesion.evidence
            )
            if cohesion.verdict == "COMPATIBLE"
            else None
        )
        method.gates[index] = replace(
            method.gates[index], cohesion=cohesion, override=override
        )
        if override is not None:
            materialise(analysis, entry_id)
            return True
        return False

    def collapse_container(structures: tuple[Structure, ...]) -> None:
        for index, branch in enumerate(structures):
            if not isinstance(branch, BranchStructure):
                continue
            if not cohesion_nodes(branch):
                continue

            collapsed = was_collapsed(branch)
            current: Phase | BranchStructure = (
                region_phase(branch) if collapsed else branch
            ) or branch

            # A phase-to-region boundary is owned by this branch. A region on
            # the left already owned the boundary during its right-side check.
            if index > 0 and isinstance(structures[index - 1], LinearStructure):
                left_structure = structures[index - 1]
                left_id = left_structure.nodeIds[-1]
                if left_id not in method.retainedCallIds:
                    left_phase = phase_for_node(left_id)
                    if left_phase is not None:
                        merged = calculate(
                            gate_index("branch-entry", left_id, branch.groupId),
                            left_phase,
                            current,
                        )
                        if merged:
                            collapsed = True
                            current = region_phase(branch) or current

            if index + 1 < len(structures):
                right_structure = structures[index + 1]
                if isinstance(right_structure, LinearStructure):
                    right_id = right_structure.nodeIds[0]
                    if right_id not in method.retainedCallIds:
                        right_phase = phase_for_node(right_id)
                        if right_phase is not None:
                            merged = calculate(
                                gate_index(
                                    "branch-convergence",
                                    branch.groupId,
                                    right_id,
                                ),
                                current,
                                right_phase,
                            )
                            if merged:
                                collapsed = True
                                current = region_phase(branch) or current
                else:
                    merged = calculate(
                        gate_index(
                            "branch-entry",
                            branch.groupId,
                            right_structure.groupId,
                        ),
                        current,
                        right_structure,
                    )
                    if merged:
                        collapsed = True

            if not collapsed:
                for arm in branch.arms:
                    collapse_container(arm)

    collapse_container(method_structure.structures)


def analyse(
    graph: Graph,
    excluded: dict[str, ExclusionReason] | None = None,
) -> Analysis:
    """Analyse every method with callees resolved before their callers."""
    excluded = excluded or {}
    structures = build_method_structures(graph, excluded)
    analysis = Analysis(
        graph=graph,
        excluded=excluded,
        structures=structures,
        methods={},
        calleeEntries=_callee_entries(graph),
    )
    def resolve(entry_id: str) -> MethodAnalysis | None:
        if entry_id in analysis.methods:
            return analysis.methods[entry_id]
        if entry_id in analysis.resolvingMethods:
            return None
        if entry_id not in analysis.structures:
            return None

        analysis.resolvingMethods.add(entry_id)
        try:
            structure = analysis.structures[entry_id]
            for call_site_id in _structure_node_ids(structure.structures):
                for callee_entry_id in analysis.calleeEntries.get(call_site_id, ()):
                    resolve(callee_entry_id)

            method = _segment_method_first_pass(analysis, entry_id)
            analysis.methods[entry_id] = method
            _collapse_regions(analysis, entry_id)
        finally:
            analysis.resolvingMethods.discard(entry_id)
        return method

    analysis.resolver = resolve
    rank = {node.id: index for index, node in enumerate(graph.nodes)}
    entry_ids = sorted(structures, key=lambda entry_id: rank.get(entry_id, 10**9))
    root_ids = [entry_id for entry_id in graph.roots if entry_id in structures]
    for entry_id in dict.fromkeys((*root_ids, *entry_ids)):
        resolve(entry_id)
    return analysis
