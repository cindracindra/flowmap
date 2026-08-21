from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable, Literal

from domain.phase_relationship import CandidateDecision, evaluate_phase_candidate
from model import Graph


ResolvedAction = Literal["MERGE", "SPLIT"]


@dataclass(frozen=True, slots=True)
class CandidateGate:
    """One boundary between consecutive, method-local operation candidates."""

    frontierId: str
    candidateId: str
    systematic: CandidateDecision
    action: Literal["MERGE", "SPLIT", "UNCERTAIN"]
    decidedBy: Literal["systematic", "llm"] = "systematic"
    llmConfidence: float | None = None
    llmEvidence: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CandidatePhase:
    id: str
    nodeIds: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CandidatePhasePlan:
    """Deterministic phase candidates plus gates awaiting semantic review."""

    orderedNodeIds: tuple[str, ...]
    phases: tuple[CandidatePhase, ...]
    gates: tuple[CandidateGate, ...]

    @property
    def ambiguousGates(self) -> tuple[CandidateGate, ...]:
        return tuple(gate for gate in self.gates if gate.action == "UNCERTAIN")


GateAnswer = tuple[ResolvedAction, float, tuple[str, ...]]
BatchGateResolver = Callable[
    [Graph, tuple[CandidateGate, ...], tuple[str, ...]],
    dict[tuple[str, str], GateAnswer],
]


def _eligible_nodes(graph: Graph, node_ids: Iterable[str]) -> tuple[str, ...]:
    nodes = {node.id: node for node in graph.nodes}
    ordered: list[str] = []
    for node_id in dict.fromkeys(node_ids):
        node = nodes.get(node_id)
        if node is None or node.type != "call":
            continue
        features = graph.semanticFeatures.get(node_id)
        if features is not None and features.role == "exception-mechanic":
            continue
        ordered.append(node_id)
    return tuple(ordered)


def _materialise_phases(
    ordered_node_ids: tuple[str, ...], gates: tuple[CandidateGate, ...]
) -> tuple[CandidatePhase, ...]:
    if not ordered_node_ids:
        return ()
    phases: list[list[str]] = [[ordered_node_ids[0]]]
    for gate in gates:
        if gate.action == "MERGE":
            phases[-1].append(gate.candidateId)
        else:
            # An uncertain boundary remains visible and reversible. It must not
            # silently acquire a positive merge bias before LLM review.
            phases.append([gate.candidateId])
    return tuple(
        CandidatePhase(f"candidate-phase-{index + 1}", tuple(nodes))
        for index, nodes in enumerate(phases)
    )


def construct_connected_candidates(
    graph: Graph, node_ids: Iterable[str]
) -> CandidatePhasePlan:
    """Stage 8: form candidates using only immediate-frontier decisions.

    ``node_ids`` must be one ordered structural scope (a straight-line scope,
    or an already-resolved branch region). Calls into another method are not
    crossed implicitly; structural scope construction owns that decision.
    """
    ordered = _eligible_nodes(graph, node_ids)
    if not ordered:
        return CandidatePhasePlan((), (), ())

    current_phase = [ordered[0]]
    gates: list[CandidateGate] = []
    for candidate_id in ordered[1:]:
        frontier_id = current_phase[-1]
        decision = evaluate_phase_candidate(
            graph, current_phase, frontier_id, candidate_id
        )
        gates.append(CandidateGate(
            frontier_id,
            candidate_id,
            decision,
            decision.action,
        ))
        if decision.action == "MERGE":
            current_phase.append(candidate_id)
        else:
            current_phase = [candidate_id]

    frozen_gates = tuple(gates)
    return CandidatePhasePlan(
        ordered,
        _materialise_phases(ordered, frozen_gates),
        frozen_gates,
    )


def refine_ambiguous_gates(
    graph: Graph,
    plan: CandidatePhasePlan,
    resolver: BatchGateResolver,
    *,
    batch_size: int = 20,
) -> CandidatePhasePlan:
    """Stage 9: resolve at most ``batch_size`` ambiguous gates per query.

    A failed or invalid LLM response leaves the gate ``UNCERTAIN``. It never
    changes a systematic MERGE or SPLIT.
    """
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    resolved: list[CandidateGate] = []
    phase_nodes: list[str] = []
    index = 0
    while index < len(plan.gates):
        if index == 0:
            phase_nodes = [plan.gates[0].frontierId]

        end = index
        ambiguous_count = 0
        while end < len(plan.gates):
            if plan.gates[end].action == "UNCERTAIN":
                if ambiguous_count == batch_size:
                    break
                ambiguous_count += 1
            end += 1
            if ambiguous_count == batch_size:
                break

        segment = plan.gates[index:end]
        answers = (
            resolver(graph, segment, tuple(phase_nodes))
            if ambiguous_count
            else {}
        )
        for gate in segment:
            updated = gate
            answer = answers.get((gate.frontierId, gate.candidateId))
            if gate.action == "UNCERTAIN" and answer is not None:
                action, confidence, evidence = answer
                updated = CandidateGate(
                    gate.frontierId,
                    gate.candidateId,
                    gate.systematic,
                    action,
                    "llm",
                    confidence,
                    evidence,
                )
            resolved.append(updated)

            if updated.action == "MERGE":
                phase_nodes.append(updated.candidateId)
            else:
                phase_nodes = [updated.candidateId]
        index = end

    gates = tuple(resolved)
    return CandidatePhasePlan(
        plan.orderedNodeIds,
        _materialise_phases(plan.orderedNodeIds, gates),
        gates,
    )
