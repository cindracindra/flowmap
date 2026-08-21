from __future__ import annotations

import json
import re

from llm.client import LLMClient, LLMError

from domain.phase_resolution import CandidateGate, GateAnswer
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
    gates: tuple[CandidateGate, ...],
    current_phase_node_ids: tuple[str, ...],
) -> tuple[str, dict[str, CandidateGate]]:
    gates_by_id = {f"gate-{index + 1}": gate for index, gate in enumerate(gates)}
    payload = {
        "initialCurrentPhase": [
            _node_evidence(graph, node_id) for node_id in current_phase_node_ids
        ],
        "gates": [
            {
                "gate_id": gate_id,
                "knownAction": None if gate.action == "UNCERTAIN" else gate.action,
                "immediateFrontier": _node_evidence(graph, gate.frontierId),
                "candidate": _node_evidence(graph, gate.candidateId),
                "systematicEvidence": {
                    "localVerdict": gate.systematic.local.verdict,
                    "cohesionVerdict": gate.systematic.cohesion.verdict,
                    "evidence": list(gate.systematic.evidence),
                    "missingEvidence": list(gate.systematic.local.missingEvidence),
                },
            }
            for gate_id, gate in gates_by_id.items()
        ],
    }
    return json.dumps(payload, ensure_ascii=False), gates_by_id


def resolve_ambiguous_phase_gates(
    client: LLMClient,
    graph: Graph,
    gates: tuple[CandidateGate, ...],
    current_phase_node_ids: tuple[str, ...],
) -> dict[tuple[str, str], GateAnswer]:
    """Resolve one ordered, single-opseq batch containing at most 20 unknowns."""
    if sum(gate.action == "UNCERTAIN" for gate in gates) > 20:
        raise ValueError("phase gate batch cannot contain more than 20 ambiguous gates")
    prompt, gates_by_id = _batch_gate_prompt(
        graph, gates, current_phase_node_ids
    )
    try:
        content = client.complete(
            role="small",
            system=_PHASE_GATE_SYSTEM_PROMPT,
            user=prompt,
            max_tokens=1280,
            json_object=True,
        ).strip()
    except LLMError:
        return {}

    try:
        parsed = json.loads(_JSON_FENCE_RE.sub("", content).strip())
    except (json.JSONDecodeError, TypeError):
        return {}

    decisions = parsed.get("decisions", []) if isinstance(parsed, dict) else []
    answers: dict[tuple[str, str], GateAnswer] = {}
    for decision in decisions if isinstance(decisions, list) else []:
        if not isinstance(decision, dict):
            continue
        gate = gates_by_id.get(decision.get("gate_id"))
        action = decision.get("action")
        if gate is None or gate.action != "UNCERTAIN" or action not in {"MERGE", "SPLIT"}:
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
        answers[(gate.frontierId, gate.candidateId)] = (
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
