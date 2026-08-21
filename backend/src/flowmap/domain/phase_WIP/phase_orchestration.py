from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass
from typing import Callable, ContextManager, Iterable

from domain.phase_resolution import (
    BatchGateResolver,
    CandidateGate,
    CandidatePhasePlan,
    construct_connected_candidates,
    refine_ambiguous_gates,
)
from domain.phase_structure import (
    OperationClassificationResult,
    classify_operation_roles,
)
from model import Graph, Phase, Transition


PhaseLabeler = Callable[[Graph, tuple[str, ...], int], str | None]
PhaseTimer = Callable[[str], ContextManager[None]]


@dataclass(frozen=True, slots=True)
class PhaseDiscoveryResult:
    """Complete Stage 10 result, including auditable intermediate decisions."""

    graph: Graph
    classification: OperationClassificationResult
    candidatePlan: CandidatePhasePlan
    phases: tuple[Phase, ...]

    @property
    def complete(self) -> bool:
        return not self.candidatePlan.ambiguousGates

    def to_dict(self) -> dict:
        return {
            "entryPoint": self.graph.entryPoint,
            "phases": [phase.to_dict() for phase in self.phases],
            "complete": self.complete,
            "unresolvedGates": [
                {
                    "frontierId": gate.frontierId,
                    "candidateId": gate.candidateId,
                    "evidence": list(gate.systematic.evidence),
                }
                for gate in self.candidatePlan.ambiguousGates
            ],
        }


def _transition(gate: CandidateGate, *, opens_phase: bool) -> Transition:
    if gate.decidedBy == "llm":
        confidence = gate.llmConfidence
        evidence = list(gate.systematic.evidence + gate.llmEvidence)
    else:
        confidence = gate.systematic.confidence
        evidence = list(gate.systematic.evidence)

    return Transition(
        subject=gate.frontierId,
        reason="data-unrelated" if opens_phase else "data-related",
        level=2,
        boundaryType="semantic-split" if opens_phase else None,
        decidedBy=gate.decidedBy,
        confidence=confidence,
        evidence=evidence,
    )


def _materialise(
    plan: CandidatePhasePlan,
    graph: Graph,
    labeler: PhaseLabeler | None,
    structural_anchors: Iterable[str],
) -> tuple[Phase, ...]:
    phase_index_by_node = {
        node_id: index
        for index, candidate in enumerate(plan.phases)
        for node_id in candidate.nodeIds
    }
    phases = [
        Phase(
            id=f"phase-{index + 1}",
            nodes=list(candidate.nodeIds),
            structuralAnchors=list(dict.fromkeys(structural_anchors)),
        )
        for index, candidate in enumerate(plan.phases)
    ]

    for gate in plan.gates:
        left_index = phase_index_by_node[gate.frontierId]
        right_index = phase_index_by_node[gate.candidateId]
        if left_index == right_index:
            phases[right_index].transitions.append(
                _transition(gate, opens_phase=False)
            )
        else:
            phases[right_index].opened_by = _transition(gate, opens_phase=True)

    if labeler is not None:
        for index, phase in enumerate(phases):
            label = labeler(graph, tuple(phase.nodes), index)
            phase.label = label.strip() if label and label.strip() else None
    return tuple(phases)


def discover_phases(
    graph: Graph,
    ordered_node_ids: Iterable[str],
    *,
    gate_resolver: BatchGateResolver | None = None,
    labeler: PhaseLabeler | None = None,
    structural_anchors: Iterable[str] = (),
    timer: PhaseTimer | None = None,
) -> PhaseDiscoveryResult:
    """Stage 10: classify, construct, refine, materialise, then label.

    ``ordered_node_ids`` is the connected structural scope selected by the
    caller (Stage 4). This explicit boundary prevents orchestration from
    accidentally comparing nodes from separate method bodies or branch arms.
    """
    measure = timer or (lambda _: nullcontext())
    classification = classify_operation_roles(graph)
    classified_graph = classification.graph
    plan = construct_connected_candidates(classified_graph, ordered_node_ids)
    if gate_resolver is not None and plan.ambiguousGates:
        with measure(
            f"Resolve {len(plan.ambiguousGates)} ambiguous gates in batches"
        ):
            plan = refine_ambiguous_gates(classified_graph, plan, gate_resolver)
    # There is no fallback verdict. Until every ambiguous gate is resolved,
    # candidate membership is useful diagnostic state but is not a final phase
    # result and must not be labelled or exported as if it were one.
    if plan.ambiguousGates:
        phases = ()
    else:
        phases = _materialise(
            plan, classified_graph, None, structural_anchors
        )
        if labeler is not None:
            with measure(f"Label {len(phases)} final phases"):
                for index, phase in enumerate(phases):
                    label = labeler(classified_graph, tuple(phase.nodes), index)
                    phase.label = label.strip() if label and label.strip() else None
    return PhaseDiscoveryResult(classified_graph, classification, plan, phases)
