from __future__ import annotations

import json
import re

from llm.client import LLMClient, LLMError

from domain.phase_resolution import GateAnswer, GateQuestion
from llm.prompt import _LABEL_PHASE_SYSTEM_PROMPT, _PHASE_GATE_SYSTEM_PROMPT
from model import Graph


_JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)

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
    payload = {
        "phaseIndex": phase_index + 1,
        "operations": [_node_evidence(graph, node_id) for node_id in node_ids],
    }
    try:
        label = client.complete(
            role="small",
            system=_LABEL_PHASE_SYSTEM_PROMPT,
            user=json.dumps(payload, ensure_ascii=False),
            max_tokens=64,
        ).strip()
    except LLMError:
        return None
    return label or None
