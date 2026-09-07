from __future__ import annotations

from typing import Any

from domain.execution_phase.method_analysis import MethodAnalysis
from model import BranchRequirement, MethodDefinition, Node

from .graph_bundle import GraphBundle


def _ordered_unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _serialize_leaf(leaf: Node) -> dict[str, Any]:
    """Serialize only the terminal target identity needed for display."""
    payload: dict[str, Any] = {"id": leaf.id, "type": "leaf"}
    if leaf.calleeFullName:
        signature_free = leaf.calleeFullName.split(":", 1)[0]
        parts = signature_free.rsplit(".", 2)
        payload["calleeFullName"] = (
            ".".join(parts[-2:]) if len(parts) >= 2 else signature_free
        )
    elif leaf.reason:
        payload["reason"] = leaf.reason
    return payload


def _serialize_method(
    method: MethodDefinition,
    analysis: MethodAnalysis | None,
    internal_entry_ids: set[str],
    leaf_ids: set[str],
) -> dict[str, Any]:
    sequence_targets: dict[str, list[str]] = {}
    for edge in method.sequenceEdges:
        sequence_targets.setdefault(edge.source, []).append(edge.target)
    invoke_targets: dict[str, list[str]] = {}
    leaf_targets: dict[str, list[str]] = {}
    for edge in method.invokeEdges:
        if edge.target in internal_entry_ids:
            invoke_targets.setdefault(edge.source, []).append(edge.target)
        elif edge.target in leaf_ids:
            leaf_targets.setdefault(edge.source, []).append(edge.target)

    calls = {
        node.id: {
            "callNodeId": node.id,
            "targetEntryIds": sorted(set(invoke_targets.get(node.id, ()))),
            "targetLeafIds": sorted(set(leaf_targets.get(node.id, ()))),
            # Sequence edge encounter order is the canonical projected CFG
            # successor order. Deduplicate without replacing it by ID order.
            "continuationIds": _ordered_unique(
                sequence_targets.get(node.id, [])
            ),
        }
        for node in method.nodes
        if node.type == "call"
    }
    exits = [
        {
            "sourceNodeId": node.id,
            "kind": node.exitKind,
            **(
                {
                    "branchRequirements": [
                        BranchRequirement(ref.groupId, ref.armLabel).to_dict()
                        for ref in node.branchArms
                    ]
                }
                if node.branchArms
                else {}
            ),
        }
        for node in method.nodes
        if node.type == "exit" and node.exitKind is not None
    ]
    phases = [] if analysis is None else [
        {
            "id": phase.id or f"{method.entryId}:phase:{index + 1}",
            "memberNodeIds": list(phase.nodes),
            **({"label": phase.label} if phase.label is not None else {}),
        }
        for index, phase in enumerate(analysis.phases)
    ]

    return {
        "entryId": method.entryId,
        "methodFullName": method.methodFullName,
        "entry": method.entry.to_dict(),
        "nodes": [node.to_dict() for node in method.nodes],
        "sequenceEdges": [edge.to_dict() for edge in method.sequenceEdges],
        "calls": calls,
        "exits": exits,
        "branchGroups": [group.to_dict() for group in method.branchGroups],
        "loopGroups": [loop.to_dict() for loop in method.loopGroups],
        "semanticFeatures": {
            node_id: features.to_dict()
            for node_id, features in sorted(method.semanticFeatures.items())
        },
        "phases": phases,
        "retainedCallNodeIds": (
            [] if analysis is None else sorted(analysis.retained_call_ids)
        ),
    }


def serialize_graph_bundle(bundle: GraphBundle) -> dict[str, Any]:
    """Combine domain topology and analysis into the public JSON payload."""

    entry_ids = set(bundle.methodsByEntryId)
    leaf_ids = set(bundle.leavesById)
    return {
        "methodsByEntryId": {
            entry_id: _serialize_method(
                method,
                bundle.phaseAnalysis.analyses_by_entry_id.get(entry_id),
                entry_ids,
                leaf_ids,
            )
            for entry_id, method in sorted(bundle.methodsByEntryId.items())
        },
        "leavesById": {
            leaf_id: _serialize_leaf(leaf)
            for leaf_id, leaf in sorted(bundle.leavesById.items())
        },
        "operationsById": {
            operation_id: operation.to_dict()
            for operation_id, operation in sorted(bundle.operationsById.items())
        },
        "callersByEntryId": {
            entry_id: list(caller_ids)
            for entry_id, caller_ids in sorted(bundle.callersByEntryId.items())
        },
        "operationIdsByMethodEntryId": {
            entry_id: list(operation_ids)
            for entry_id, operation_ids in sorted(
                bundle.operationIdsByMethodEntryId.items()
            )
        },
    }
