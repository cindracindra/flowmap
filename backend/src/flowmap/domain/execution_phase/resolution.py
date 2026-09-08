"""Build compact, current LLM questions for unresolved phase boundaries."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from typing import Any, Callable, Literal, Mapping

from domain.execution_phase.method_analysis import MethodAnalysis
from domain.execution_phase.semantic import SemanticSignature, build_core_signature
from model import MethodDefinition, Node, NodeSemanticFeatures, Phase, UnresolvedGate


UnresolvedReason = Literal["INSUFFICIENT_EVIDENCE", "CONFLICTING_EVIDENCE"]
ResolvedGateAction = Literal["MERGE", "SPLIT"]
GateAnswer = tuple[ResolvedGateAction, float, tuple[str, ...]]


@dataclass(frozen=True, slots=True)
class ResolvedGateDecision:
    """One accepted LLM decision, retained for repeated-run evaluation."""

    question_id: str
    gate_id: str
    method_entry_id: str
    method_full_name: str
    left_id: str
    right_id: str
    boundary_kind: str
    reason_code: UnresolvedReason
    action: ResolvedGateAction
    confidence: float
    evidence: tuple[str, ...]
    systematic_confidence: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class UnresolvedGateQuestion:
    """Compact boundary snapshot prepared immediately before an LLM request."""

    id: str
    method_entry_id: str
    method_full_name: str
    gate: UnresolvedGate
    reason_code: UnresolvedReason
    left_signature: SemanticSignature
    right_signature: SemanticSignature
    left_operations: tuple[Mapping[str, Any], ...]
    right_operations: tuple[Mapping[str, Any], ...]
    left_missing_observations: tuple[str, ...]
    right_missing_observations: tuple[str, ...]

    def to_prompt_payload(self) -> dict[str, Any]:
        """Serialize only information needed for the semantic decision."""
        observed_overlap: dict[str, float] = {}
        supporting_evidence: list[str] = []
        evidence_descriptions = {
            "direct-data-flow": "Data flows directly from the left group to the right group",
            "directional-write-read": (
                "The left group writes a field that the right group reads"
            ),
        }
        for item in self.gate.evidence:
            dimension, separator, raw_score = item.partition(":")
            if separator and dimension in _SIGNATURE_PAYLOAD_NAMES:
                try:
                    observed_overlap[_SIGNATURE_PAYLOAD_NAMES[dimension]] = float(
                        raw_score
                    )
                    continue
                except ValueError:
                    pass
            if item in evidence_descriptions:
                supporting_evidence.append(evidence_descriptions[item])
        return {
            "gateId": self.gate.id,
            "method": self.method_full_name,
            "boundaryKind": self.gate.kind,
            "systematicAssessment": {
                "status": self.reason_code.replace("_", " ").lower(),
                "observedSemanticOverlap": observed_overlap,
                "supportingEvidence": supporting_evidence,
                "coreIdentityFullyObservedAndDisjoint": (
                    "complete-core-identity-disjoint" in self.gate.evidence
                ),
                "missingObservations": {
                    "left": [
                        _SIGNATURE_PAYLOAD_NAMES[item]
                        for item in self.left_missing_observations
                    ],
                    "right": [
                        _SIGNATURE_PAYLOAD_NAMES[item]
                        for item in self.right_missing_observations
                    ],
                },
            },
            "leftGroup": {
                "coreSignature": signature_payload(self.left_signature),
                "operations": list(self.left_operations),
            },
            "rightGroup": {
                "coreSignature": signature_payload(self.right_signature),
                "operations": list(self.right_operations),
            },
        }


BatchGateResolver = Callable[
    [tuple[UnresolvedGateQuestion, ...]],
    dict[str, GateAnswer],
]


def _phase_containing(analysis: MethodAnalysis, node_id: str) -> Phase | None:
    return next((phase for phase in analysis.phases if node_id in phase.nodes), None)


def _phase_signature(method: MethodDefinition, phase: Phase) -> SemanticSignature:
    return build_core_signature(
        SemanticSignature.from_operation(
            method.semanticFeatures.get(node_id, NodeSemanticFeatures())
        )
        for node_id in phase.nodes
    )


_SIGNATURE_PAYLOAD_NAMES = {
    "receivers": "receivers",
    "inputs": "inputs",
    "arguments": "arguments",
    "fields_read": "fieldsRead",
    "fields_written": "fieldsWritten",
    "domain_types": "domainTypes",
    "method_terms": "methodTerms",
    "output_types": "outputTypes",
}


def signature_payload(signature: SemanticSignature) -> dict[str, list[str]]:
    """Return the canonical compact signature used by phase LLM payloads."""
    dimensions = signature.dimensions()
    return {
        output_name: sorted(dimensions[input_name])
        for input_name, output_name in _SIGNATURE_PAYLOAD_NAMES.items()
        if dimensions[input_name]
    }


def _missing_observations(signature: SemanticSignature) -> tuple[str, ...]:
    return tuple(sorted(set(signature.dimensions()) - set(signature.observed_features)))


def operation_context(node: Node | None) -> Mapping[str, Any]:
    """Return the canonical ordered-operation context used by phase payloads."""
    if node is None:
        return {}
    context: dict[str, Any] = {}
    if node.calleeFullName:
        context["callee"] = node.calleeFullName
    if node.code:
        context["code"] = node.code
    return context


def _reason_code(gate: UnresolvedGate) -> UnresolvedReason:
    if "complete-core-identity-disjoint" in gate.evidence and any(
        item != "complete-core-identity-disjoint" for item in gate.evidence
    ):
        return "CONFLICTING_EVIDENCE"
    return "INSUFFICIENT_EVIDENCE"


def build_gate_question(
    gate: UnresolvedGate,
    method: MethodDefinition,
    analysis: MethodAnalysis,
) -> UnresolvedGateQuestion | None:
    """Build fresh context for a gate that still separates two phases."""
    if analysis.method_entry_id != method.entryId:
        raise ValueError(
            f"analysis for {analysis.method_entry_id!r} does not match "
            f"method {method.entryId!r}"
        )

    left_phase = _phase_containing(analysis, gate.left_id)
    right_phase = _phase_containing(analysis, gate.right_id)
    if left_phase is None or right_phase is None or left_phase is right_phase:
        return None

    left_signature = _phase_signature(method, left_phase)
    right_signature = _phase_signature(method, right_phase)
    return UnresolvedGateQuestion(
        id=f"question:{method.entryId}:{gate.id}",
        method_entry_id=method.entryId,
        method_full_name=method.methodFullName,
        gate=gate,
        reason_code=_reason_code(gate),
        left_signature=left_signature,
        right_signature=right_signature,
        left_operations=tuple(
            operation_context(next(
                (node for node in method.nodes if node.id == node_id), None
            ))
            for node_id in left_phase.nodes
        ),
        right_operations=tuple(
            operation_context(next(
                (node for node in method.nodes if node.id == node_id), None
            ))
            for node_id in right_phase.nodes
        ),
        left_missing_observations=_missing_observations(left_signature),
        right_missing_observations=_missing_observations(right_signature),
    )


def build_gate_questions(
    methods_by_entry_id: dict[str, MethodDefinition],
    analyses_by_entry_id: dict[str, MethodAnalysis],
) -> tuple[UnresolvedGateQuestion, ...]:
    """Collect all still-relevant first-pass gates in method order."""
    questions: list[UnresolvedGateQuestion] = []
    for entry_id, analysis in analyses_by_entry_id.items():
        method = methods_by_entry_id.get(entry_id)
        if method is None:
            continue
        for gate in analysis.unresolved_gates:
            gate_context = build_gate_question(
                gate,
                method,
                analysis,
            )
            if gate_context is not None:
                questions.append(gate_context)
    return tuple(questions)


def _current_gate(
    analysis: MethodAnalysis,
    gate_id: str,
) -> UnresolvedGate | None:
    return next((gate for gate in analysis.unresolved_gates if gate.id == gate_id), None)


def _merge_gate_phases(
    analysis: MethodAnalysis,
    gate: UnresolvedGate,
) -> MethodAnalysis:
    left = _phase_containing(analysis, gate.left_id)
    right = _phase_containing(analysis, gate.right_id)
    if left is None or right is None or left is right:
        return analysis

    merged = Phase(
        nodes=list(dict.fromkeys((*left.nodes, *right.nodes))),
        id=left.id,
        label=left.label,
    )
    phases: list[Phase] = []
    inserted = False
    for phase in analysis.phases:
        if phase is left or phase is right:
            if not inserted:
                phases.append(merged)
                inserted = True
            continue
        phases.append(phase)
    return replace(analysis, phases=tuple(phases))


def _remove_gate(
    analysis: MethodAnalysis,
    gate_id: str,
) -> MethodAnalysis:
    return replace(
        analysis,
        unresolved_gates=tuple(
            gate for gate in analysis.unresolved_gates if gate.id != gate_id
        ),
    )


def resolve_uncertain_gates(
    methods_by_entry_id: dict[str, MethodDefinition],
    analyses_by_entry_id: dict[str, MethodAnalysis],
    resolver: BatchGateResolver,
    *,
    batch_size: int | None = None,
    decision_sink: list[ResolvedGateDecision] | None = None,
) -> int:
    """Resolve valid LLM answers and retain every unanswered boundary."""
    if batch_size is not None and batch_size < 1:
        raise ValueError("batch_size must be positive")

    questions = build_gate_questions(
        methods_by_entry_id,
        analyses_by_entry_id,
    )
    resolved_count = 0
    effective_batch_size = batch_size or max(1, len(questions))
    for start in range(0, len(questions), effective_batch_size):
        batch = questions[start : start + effective_batch_size]
        answers = resolver(batch)
        for question in batch:
            answer = answers.get(question.id)
            if answer is None or not isinstance(answer, tuple) or len(answer) != 3:
                continue
            action, confidence, evidence = answer
            if action not in {"MERGE", "SPLIT"}:
                continue
            if not isinstance(confidence, (int, float)) or isinstance(confidence, bool):
                continue
            if not isinstance(evidence, tuple) or not all(
                isinstance(item, str) for item in evidence
            ):
                continue

            analysis = analyses_by_entry_id.get(question.method_entry_id)
            method = methods_by_entry_id.get(question.method_entry_id)
            if analysis is None or method is None:
                continue
            gate = _current_gate(analysis, question.gate.id)
            if gate is None:
                continue
            current_question = build_gate_question(
                gate,
                method,
                analysis,
            )
            if current_question is None:
                analyses_by_entry_id[question.method_entry_id] = _remove_gate(
                    analysis, gate.id
                )
                continue

            if decision_sink is not None:
                decision_sink.append(ResolvedGateDecision(
                    question_id=current_question.id,
                    gate_id=gate.id,
                    method_entry_id=current_question.method_entry_id,
                    method_full_name=current_question.method_full_name,
                    left_id=gate.left_id,
                    right_id=gate.right_id,
                    boundary_kind=gate.kind,
                    reason_code=current_question.reason_code,
                    action=action,
                    confidence=float(confidence),
                    evidence=evidence,
                    systematic_confidence=gate.confidence,
                ))

            if action == "MERGE":
                analysis = _merge_gate_phases(analysis, gate)
            analyses_by_entry_id[question.method_entry_id] = _remove_gate(
                analysis, gate.id
            )
            resolved_count += 1

    return resolved_count
