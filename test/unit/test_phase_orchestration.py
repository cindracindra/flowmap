from __future__ import annotations

import sys
from pathlib import Path

FLOWMAP_SRC = Path(__file__).resolve().parents[2] / "backend" / "src" / "flowmap"
sys.path.insert(0, str(FLOWMAP_SRC))

from domain.phase_orchestration import discover_phases  # noqa: E402
from model import Graph  # noqa: E402


def _graph() -> Graph:
    return Graph.from_dict({
        "entryPoint": "Checkout.run",
        "nodes": [
            {"id": "entry", "type": "entry", "calleeFullName": "Checkout.run"},
            {"id": "validate", "type": "call", "calleeFullName": "Order.validate"},
            {"id": "reserve", "type": "call", "calleeFullName": "Stock.reserve"},
            {"id": "notify", "type": "call", "calleeFullName": "Mail.send"},
        ],
        "edges": [
            {"from": "entry", "to": "validate", "type": "sequence"},
            {"from": "validate", "to": "reserve", "type": "sequence"},
            {"from": "reserve", "to": "notify", "type": "sequence"},
        ],
        "semanticFeatures": {
            "validate": {
                "receiver": "order", "inputIdentifiers": ["order"],
                "methodTerms": ["validate", "order"],
                "observedFeatures": ["receiver", "arguments", "inputs"],
            },
            "reserve": {
                "receiver": "stock", "methodTerms": ["reserve", "stock"],
                "observedFeatures": ["methodTerms"],
            },
            "notify": {
                "receiver": "mailer", "inputIdentifiers": ["message"],
                "methodTerms": ["notify", "customer"],
                "observedFeatures": ["receiver", "arguments", "inputs"],
            },
        },
    })


def test_stage_10_orders_membership_resolution_before_labelling() -> None:
    events = []

    def resolve(graph, gates, current_nodes):
        events.append(("gate", tuple(g.candidateId for g in gates), current_nodes))
        return {
            (gate.frontierId, gate.candidateId): (
                ("MERGE", 0.9, ("llm:checkout preparation",))
                if gate.candidateId == "reserve"
                else ("SPLIT", 0.9, ("llm:notification is next subprocess",))
            )
            for gate in gates if gate.action == "UNCERTAIN"
        }

    def label(graph, node_ids, index):
        events.append(("label", node_ids, index))
        return "Order Preparation" if index == 0 else "Customer Notification"

    result = discover_phases(
        _graph(),
        ("validate", "reserve", "notify"),
        gate_resolver=resolve,
        labeler=label,
        structural_anchors=("straight:Checkout.run:0",),
    )

    assert [phase.nodes for phase in result.phases] == [
        ["validate", "reserve"], ["notify"]
    ]
    assert [phase.label for phase in result.phases] == [
        "Order Preparation", "Customer Notification"
    ]
    assert [event[0] for event in events] == ["gate", "label", "label"]
    assert result.phases[0].structuralAnchors == ["straight:Checkout.run:0"]
    assert result.phases[0].transitions[0].decidedBy == "llm"
    assert result.phases[1].opened_by.decidedBy == "llm"


def test_unresolved_gate_makes_result_incomplete_without_fallback_phase() -> None:
    result = discover_phases(
        _graph(),
        ("validate", "reserve"),
        gate_resolver=lambda *_: {},
    )

    assert result.phases == ()
    assert result.complete is False
    exported = result.to_dict()
    assert exported["complete"] is False
    assert exported["unresolvedGates"][0]["candidateId"] == "reserve"


def test_exception_classification_runs_before_candidate_construction() -> None:
    graph = Graph.from_dict({
        "nodes": [
            {"id": "work", "type": "call", "calleeFullName": "Order.save"},
            {
                "id": "error", "type": "call", "deadEnd": True,
                "calleeFullName": "IllegalStateException.<init>:void()",
                "branchArms": [{"groupId": "guard", "armLabel": "if"}],
            },
        ],
        "edges": [{"from": "work", "to": "error", "type": "sequence"}],
        "branchGroups": [{
            "id": "guard", "kind": "IF", "branchPointIds": ["work"],
            "arms": [{"label": "if", "empty": False, "terminus": "throw", "firstCallId": "error"}],
        }],
    })

    result = discover_phases(graph, ("work", "error"))

    assert result.classification.operations["error"].role == "exception-mechanic"
    assert result.phases[0].nodes == ["work"]
