"""Stage 5: resolve every uncertain phase gate in codebase-wide batches."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Callable, Literal

from domain.phase_segmentation import (
    Analysis,
    Gate,
    GateOverride,
    materialise,
)
from model import Graph


ResolvedAction = Literal["MERGE", "SPLIT"]
GateAnswer = tuple[ResolvedAction, float, tuple[str, ...]]


@dataclass(frozen=True, slots=True)
class GateQuestion:
    """One independent LLM question and its route back to a stored gate."""

    id: str
    methodEntryId: str
    gateIndex: int
    gate: Gate
    currentPhaseNodeIds: tuple[str, ...]


BatchGateResolver = Callable[
    [Graph, tuple[GateQuestion, ...]],
    dict[str, GateAnswer],
]


def _current_phase_node_ids(
    analysis: Analysis,
    method_entry_id: str,
    frontier_id: str,
) -> tuple[str, ...]:
    method = analysis.methods[method_entry_id]
    return next(
        (
            tuple(phase.nodes)
            for phase in method.phases
            if frontier_id in phase.nodes
        ),
        (frontier_id,),
    )


def collect_uncertain_gates(analysis: Analysis) -> tuple[GateQuestion, ...]:
    """Collect every unresolved adjacency gate once across the whole graph."""
    questions: list[GateQuestion] = []
    for method_entry_id, method in analysis.methods.items():
        for gate_index, gate in enumerate(method.gates):
            if gate.kind != "adjacency" or gate.action != "UNCERTAIN":
                continue
            current_phase = _current_phase_node_ids(
                analysis, method_entry_id, gate.frontierId
            )
            # A containing region override has already fixed this membership.
            # The uncertain inner gate no longer controls a phase boundary.
            if gate.candidateId in current_phase:
                continue
            questions.append(GateQuestion(
                id=f"q-{len(questions) + 1}",
                methodEntryId=method_entry_id,
                gateIndex=gate_index,
                gate=gate,
                currentPhaseNodeIds=current_phase,
            ))
    return tuple(questions)


def resolve_uncertain_gates(
    analysis: Analysis,
    resolver: BatchGateResolver,
    *,
    batch_size: int = 20,
) -> int:
    """Ask the LLM about every uncertain gate and apply valid answers.

    Questions from every method share one work list before batching. Each
    question is independent, so a partial or failed batch leaves only its
    unanswered gates as fallback splits. The return value is the number of
    gates resolved by the LLM.
    """
    if batch_size < 1:
        raise ValueError("batch_size must be positive")

    questions = collect_uncertain_gates(analysis)
    affected_methods: set[str] = set()
    resolved_count = 0

    for start in range(0, len(questions), batch_size):
        batch = questions[start : start + batch_size]
        answers = resolver(analysis.graph, batch)

        for question in batch:
            answer = answers.get(question.id)
            if answer is None:
                continue
            action, confidence, evidence = answer
            if action not in {"MERGE", "SPLIT"}:
                continue
            if not isinstance(confidence, (int, float)) or isinstance(confidence, bool):
                continue

            confidence = max(0.0, min(1.0, float(confidence)))
            method = analysis.methods[question.methodEntryId]
            current = method.gates[question.gateIndex]
            if (
                current.kind != "adjacency"
                or current.action != "UNCERTAIN"
                or current.frontierId != question.gate.frontierId
                or current.candidateId != question.gate.candidateId
            ):
                continue

            method.gates[question.gateIndex] = replace(
                current,
                override=GateOverride(
                    action,
                    "llm",
                    confidence,
                    tuple(evidence),
                ),
            )
            affected_methods.add(question.methodEntryId)
            resolved_count += 1

    for method_entry_id in analysis.methods:
        if method_entry_id in affected_methods:
            materialise(analysis, method_entry_id)
    return resolved_count
