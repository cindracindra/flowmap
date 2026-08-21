"""UPDATED Stages 3 and 4: resolve every method, callees before callers.

Stage 3 is not a separate pass. A method asks for each callee's phase count while
segmenting, so every callee finishes before its caller -- which is post-order,
produced by the recursion rather than by an ordering step.

What is stored per method is a flat ordered `sequence` plus its `gates`. Segments
and counts are projections of those two, recomputed on demand, so when the LLM
later changes a gate everything downstream follows with no invalidation.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Callable, Literal

from domain.phase_exclusion import ExclusionReason
from domain.phase_relationship import (
    CandidateAction,
    CohesionDecision,
    RelationshipDecision,
    build_phase_signature,
    evaluate_phase_cohesion,
    evaluate_relationship,
)
from domain.phase_scopes import Scope, build_scopes
from model import Graph


GateKind = Literal[
    "adjacency",                # two consecutive operations in one scope
    "branch-entry",             # a scope boundary entering a region
    "branch-convergence",       # a scope boundary leaving one
    "arm-alternation",          # between two alternatives of the same branch
    "region-split",             # within one region: a fork, a merge, a loop edge
    "nested-region-retained",   # either side of a retained call site
]

DecisionSource = Literal["systematic", "llm", "region", "fallback", "structural"]


@dataclass(frozen=True, slots=True)
class GateOverride:
    """A decision made outside the systematic rules.

    The only thing about a gate that is stored rather than derived, because it
    is the only thing that cannot be recomputed from the evidence. Two things
    produce one: the LLM resolving an uncertain adjacency, and the region check
    collapsing a structural boundary.
    """

    action: Literal["MERGE", "SPLIT"]
    decidedBy: Literal["llm", "region"]
    confidence: float
    evidence: tuple[str, ...] = ()


def systematic_action(
    local: RelationshipDecision | None, cohesion: CohesionDecision | None
) -> CandidateAction:
    """Local evidence decides; cohesion may veto but never creates a merge."""
    if local is None or cohesion is None:
        return "SPLIT"
    if local.verdict == "UNRELATED":
        return "SPLIT"
    if local.verdict == "RELATED" and cohesion.verdict != "INCOMPATIBLE":
        return "MERGE"
    return "UNCERTAIN"


@dataclass(frozen=True, slots=True)
class Gate:
    """One boundary. `action` and `decidedBy` are projections, never stored."""

    frontierId: str
    candidateId: str
    kind: GateKind = "adjacency"
    local: RelationshipDecision | None = None
    cohesion: CohesionDecision | None = None
    override: GateOverride | None = None

    @property
    def action(self) -> CandidateAction:
        # Checked before `kind`, so a structural boundary can be collapsed
        # without rewriting what the boundary *is* -- Stage 6 and the validator
        # both need to find region boundaries after they have been decided.
        if self.override is not None:
            return self.override.action
        if self.kind != "adjacency":
            return "SPLIT"
        return systematic_action(self.local, self.cohesion)

    @property
    def decidedBy(self) -> DecisionSource:
        # "fallback" only means anything once Stage 5 has had its chance, which
        # is why this is read at export rather than stored during the walk.
        if self.override is not None:
            return self.override.decidedBy
        if self.kind != "adjacency":
            return "structural"
        return "fallback" if self.action == "UNCERTAIN" else "systematic"


@dataclass(frozen=True, slots=True)
class Segment:
    """A group of one method's own operations."""

    nodeIds: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RetainedCall:
    """A hole in the sequence: the callee at this site contributes its own phases."""

    callSiteId: str


SegmentItem = Segment | RetainedCall


@dataclass(frozen=True, slots=True)
class MethodSegments:
    methodEntryId: str
    sequence: tuple[str, ...]
    gates: tuple[Gate, ...]


EMPTY = MethodSegments("", (), ())


@dataclass(slots=True)
class Analysis:
    """Everything Stages 5 to 8 read. `segments` is the durable state."""

    graph: Graph
    excluded: dict[str, ExclusionReason]
    scopes: dict[str, tuple[Scope, ...]]
    segments: dict[str, MethodSegments]

    # Call site id -> the entry nodes of the in-project methods it invokes.
    # Nothing to do with phases: it is a precomputed index of the `invoke` edges
    # that point at an `entry`, so `callee_count` never rescans the edges. A call
    # to an external or unresolved method invokes a `leaf`, not an `entry`, and
    # so appears here not at all.
    calleeEntries: dict[str, tuple[str, ...]] = field(default_factory=dict)

    # Set while `analyse` runs, so a count can pull in a callee that has not been
    # segmented yet. Everything is resolved by the time it returns.
    resolver: Callable[[str], MethodSegments] | None = field(default=None, repr=False)

    counts: dict[str, int] = field(default_factory=dict, repr=False)

    def clear_counts(self) -> None:
        """Call after any change to `gates` -- Stage 5's answers, Stage 6's fixups."""
        self.counts.clear()


def materialise(analysis: Analysis, entry_id: str) -> tuple[SegmentItem, ...]:
    """Project one method's sequence and gates into ordered items."""
    method = analysis.segments.get(entry_id)
    if method is None or not method.sequence:
        return ()

    gate_before = {gate.candidateId: gate for gate in method.gates}
    items: list[SegmentItem] = []
    current: list[str] = []

    for node_id in method.sequence:
        if callee_count(analysis, node_id) > 1:
            if current:
                items.append(Segment(tuple(current)))
                current = []
            items.append(RetainedCall(node_id))
            continue

        gate = gate_before.get(node_id)
        if current and gate is not None and gate.action == "MERGE":
            current.append(node_id)
        else:
            if current:
                items.append(Segment(tuple(current)))
            current = [node_id]

    if current:
        items.append(Segment(tuple(current)))
    return tuple(items)


def phase_count(analysis: Analysis, entry_id: str) -> int:
    """How many phases this method contributes, retained callees included."""
    if entry_id not in analysis.segments:
        if analysis.resolver is None:
            return 0
        analysis.resolver(entry_id)
        if entry_id not in analysis.segments:
            return 0  # recursion cutoff -- deliberately not cached
    if entry_id not in analysis.counts:
        analysis.counts[entry_id] = sum(
            1 if isinstance(item, Segment) else callee_count(analysis, item.callSiteId)
            for item in materialise(analysis, entry_id)
        )
    return analysis.counts[entry_id]


def callee_count(analysis: Analysis, call_site_id: str) -> int:
    """0 when nothing visible happens inside this call.

    A polymorphic call site has several callees and so several counts. The
    maximum wins, deliberately: one multi-phase implementation retains the site,
    and "use the first implementation" would not be deterministic across runs.
    """
    return max(
        (
            phase_count(analysis, entry)
            for entry in analysis.calleeEntries.get(call_site_id, ())
        ),
        default=0,
    )


def _scope_boundary_kind(previous: Scope, current: Scope) -> GateKind:
    """What kind of boundary two consecutive scopes meet at, from their tags.
    """
    shared_group_differing_arm = any(
        group == other_group and arm != other_arm
        for group, arm in previous.tags
        for other_group, other_arm in current.tags
    )
    if shared_group_differing_arm:
        return "arm-alternation"
    if current.tags > previous.tags:
        return "branch-entry"
    if current.tags < previous.tags:
        return "branch-convergence"
    if current.tags == previous.tags:
        return "region-split"
    return "branch-entry"


def _segment_method(analysis: Analysis, entry_id: str) -> MethodSegments:
    """Stage 4 for one method. Its callees must already be resolved.

    Four things create a boundary, and only the last consults evidence: the start
    of a scope, the end of a scope, a retained call site, and a gate resolving
    SPLIT or staying UNCERTAIN. The first three are emitted as gates rather than
    left implicit, so every boundary can say why it exists.
    """
    graph = analysis.graph
    sequence: list[str] = []
    gates: list[Gate] = []

    active: list[str] = []
    previous_id: str | None = None
    previous_scope: Scope | None = None
    previous_retained = False

    for scope in analysis.scopes.get(entry_id, ()):
        for node_id in scope.nodeIds:
            retained = callee_count(analysis, node_id) > 1

            if previous_id is None:
                pass
            elif previous_retained or retained:
                gates.append(Gate(previous_id, node_id, "nested-region-retained"))
                active = []
            elif previous_scope is not scope:
                gates.append(
                    Gate(previous_id, node_id, _scope_boundary_kind(previous_scope, scope))
                )
                active = []
            else:
                local = evaluate_relationship(graph, previous_id, node_id)
                cohesion = evaluate_phase_cohesion(
                    graph, build_phase_signature(graph, active), node_id
                )
                gates.append(Gate(previous_id, node_id, "adjacency", local, cohesion))
                if systematic_action(local, cohesion) != "MERGE":
                    active = []

            if not retained:
                active.append(node_id)

            sequence.append(node_id)
            previous_id, previous_scope, previous_retained = node_id, scope, retained

    return MethodSegments(entry_id, tuple(sequence), tuple(gates))


def _callee_entries(graph: Graph) -> dict[str, tuple[str, ...]]:
    nodes = {node.id: node for node in graph.nodes}
    found: dict[str, list[str]] = defaultdict(list)
    for edge in graph.edges:
        target = nodes.get(edge.target)
        if edge.type == "invoke" and target is not None and target.type == "entry":
            found[edge.source].append(edge.target)
    return {call_id: tuple(sorted(entries)) for call_id, entries in found.items()}


def analyse(graph: Graph, excluded: dict[str, ExclusionReason] | None = None) -> Analysis:
    """UPDATED Stages 3 and 4: segment every method reachable from a root.

    Driving from `graph.roots` reaches every method that has any content -- the
    unreachable remainder are the orphans, which have no surviving executable
    flow. Roots are already sorted, which is what keeps the recursion cutoff
    deterministic.
    """
    excluded = excluded or {}
    analysis = Analysis(
        graph=graph,
        excluded=excluded,
        scopes=build_scopes(graph, excluded),
        segments={},
        calleeEntries=_callee_entries(graph),
    )
    in_progress: set[str] = set()

    def resolve(entry_id: str) -> MethodSegments:
        if entry_id in analysis.segments:
            return analysis.segments[entry_id]
        if entry_id in in_progress:
            # The back call contributes nothing here. Returned without caching,
            # so the method's real result still lands when its outer invocation
            # finishes.
            return EMPTY
        in_progress.add(entry_id)
        try:
            result = _segment_method(analysis, entry_id)
        finally:
            in_progress.discard(entry_id)
        analysis.segments[entry_id] = result
        return result

    analysis.resolver = resolve
    for root_id in graph.roots:
        resolve(root_id)
    return analysis
