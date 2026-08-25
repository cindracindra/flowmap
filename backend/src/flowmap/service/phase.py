from __future__ import annotations

import json
import re
import sys

from llm.client import LLMClient, LLMError

from domain.phase_resolution import GateAnswer, GateQuestion
from llm.prompt import _LABEL_PHASE_SYSTEM_PROMPT, _PHASE_GATE_SYSTEM_PROMPT
from model import Graph
from service.phase_label_format import normalise_phase_label, valid_phase_label


_JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)
_LABEL_RETRY_SYSTEM_PROMPT = (
    _LABEL_PHASE_SYSTEM_PROMPT
    + " Your previous response was blank or invalid. Use 2-6 words and return "
      "only the corrected label."
)


def _phase_label_issue(
    graph: Graph,
    phase_index: int,
    issue: str,
    *,
    response: str | None = None,
) -> None:
    operation = graph.entryPoint or "<unknown operation>"
    detail = f"; response={response!r}" if response is not None else ""
    print(
        f"[phase-label] {operation} phase-{phase_index + 1}: {issue}{detail}",
        file=sys.stderr,
    )

def _node_evidence(graph: Graph, node_id: str) -> dict:
    node = next((candidate for candidate in graph.nodes if candidate.id == node_id), None)
    features = graph.semanticFeatures.get(node_id)
    return {
        "id": node_id,
        "callee": node.calleeFullName if node else None,
        "code": node.code if node else None,
        "receiver": features.receiver if features else None,
        "arguments": list(features.arguments) if features else [],
        "inputs": list(features.inputIdentifiers) if features else [],
        "fieldsRead": list(features.fieldsRead) if features else [],
        "fieldsWritten": list(features.fieldsWritten) if features else [],
        "domainTypes": list(features.domainTypes) if features else [],
        "methodTerms": list(features.methodTerms) if features else [],
    }


def _has_semantic_evidence(operation: dict) -> bool:
    return any(operation.get(field) for field in (
        "callee", "code", "receiver", "arguments", "inputs", "fieldsRead",
        "fieldsWritten", "domainTypes", "methodTerms",
    ))


def _batch_gate_prompt(
    graph: Graph,
    questions: tuple[GateQuestion, ...],
) -> str:
    operation_ids = tuple(dict.fromkeys(
        node_id
        for question in questions
        for node_id in (
            *question.currentPhaseNodeIds,
            question.gate.frontierId,
            question.gate.candidateId,
        )
    ))
    payload = {
        "operations": {
            node_id: _node_evidence(graph, node_id)
            for node_id in operation_ids
        },
        "questions": [
            {
                "id": question.id,
                "methodEntryId": question.methodEntryId,
                "currentPhase": list(question.currentPhaseNodeIds),
                "frontier": question.gate.frontierId,
                "candidate": question.gate.candidateId,
                "systematic": {
                    "localVerdict": question.gate.local.verdict,
                    "cohesionVerdict": question.gate.cohesion.verdict,
                    "evidence": list(dict.fromkeys((
                        *question.gate.local.evidence,
                        *question.gate.cohesion.evidence,
                    ))),
                    "missingEvidence": list(
                        question.gate.local.missingEvidence
                    ),
                },
            }
            for question in questions
        ],
    }
    return json.dumps(payload, ensure_ascii=False)


def resolve_phase_gate_batch(
    client: LLMClient,
    graph: Graph,
    questions: tuple[GateQuestion, ...],
) -> dict[str, GateAnswer]:
    """Resolve one batch of independent questions from across the codebase."""
    if not questions:
        return {}
    prompt = _batch_gate_prompt(graph, questions)
    questions_by_id = {question.id: question for question in questions}
    try:
        content = client.complete(
            role="small",
            system=_PHASE_GATE_SYSTEM_PROMPT,
            user=prompt,
            max_tokens=max(512, min(4096, 160 * len(questions))),
            json_object=True,
        ).strip()
    except LLMError:
        return {}

    try:
        parsed = json.loads(_JSON_FENCE_RE.sub("", content).strip())
    except (json.JSONDecodeError, TypeError):
        return {}

    decisions = parsed.get("decisions", []) if isinstance(parsed, dict) else []
    answers: dict[str, GateAnswer] = {}
    for decision in decisions if isinstance(decisions, list) else []:
        if not isinstance(decision, dict):
            continue
        question_id = decision.get("id")
        action = decision.get("action")
        if question_id not in questions_by_id or action not in {"MERGE", "SPLIT"}:
            continue
        confidence = decision.get("confidence", 0.5)
        if not isinstance(confidence, (int, float)) or isinstance(confidence, bool):
            confidence = 0.5
        confidence = max(0.0, min(1.0, float(confidence)))
        reason = decision.get("reason")
        evidence = (
            (f"llm:{reason.strip()}",)
            if isinstance(reason, str) and reason.strip()
            else ("llm-semantic-decision",)
        )
        answers[question_id] = (
            action,
            confidence,
            evidence,
        )
    return answers


def label_phase(
    client: LLMClient,
    graph: Graph,
    node_ids: tuple[str, ...],
    phase_index: int,
) -> str | None:
    """Name one already-final phase without reconsidering its membership."""
    if not node_ids:
        _phase_label_issue(graph, phase_index, "invalid input: phase has no members")
        return None
    graph_node_ids = {node.id for node in graph.nodes}
    missing_node_ids = [node_id for node_id in node_ids if node_id not in graph_node_ids]
    operations = [_node_evidence(graph, node_id) for node_id in node_ids]
    evidence_count = sum(_has_semantic_evidence(operation) for operation in operations)
    if missing_node_ids:
        _phase_label_issue(
            graph,
            phase_index,
            f"missing graph nodes ({len(missing_node_ids)}/{len(node_ids)}): "
            + ", ".join(missing_node_ids[:5]),
        )
    if evidence_count == 0:
        _phase_label_issue(
            graph,
            phase_index,
            f"invalid input: no semantic evidence for {len(node_ids)} members",
        )
        return None
    payload = {
        "phaseIndex": phase_index + 1,
        "operations": operations,
    }
    user_prompt = json.dumps(payload, ensure_ascii=False)
    for attempt in range(2):
        try:
            raw_response = client.complete(
                role="small",
                system=(
                    _LABEL_PHASE_SYSTEM_PROMPT
                    if attempt == 0 else _LABEL_RETRY_SYSTEM_PROMPT
                ),
                user=user_prompt,
                max_tokens=64,
            )
        except LLMError as exc:
            _phase_label_issue(graph, phase_index, f"provider error: {exc}")
            return None
        response = normalise_phase_label(raw_response)
        if response and valid_phase_label(response):
            return response
        issue = "blank response" if not response else "invalid format"
        if attempt == 0:
            _phase_label_issue(
                graph, phase_index, f"{issue}; retrying once", response=response,
            )
            continue
        _phase_label_issue(
            graph,
            phase_index,
            f"{issue} after retry (expected a single 2-6 word label)",
            response=response,
        )
    return None
