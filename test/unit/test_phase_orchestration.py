from __future__ import annotations

import sys
from pathlib import Path

FLOWMAP_SRC = Path(__file__).resolve().parents[2] / "backend" / "src" / "flowmap"
sys.path.insert(0, str(FLOWMAP_SRC))

from domain.cfg_flattening import flatten_cfg  # noqa: E402
from domain.phase_orchestration import (  # noqa: E402
    analyse_codebase_phases,
    discover_phases,
)
from model import Graph  # noqa: E402


def _graph() -> Graph:
    return Graph.from_dict({
        "entryPoint": "Checkout.run",
        "roots": ["entry"],
        "nodes": [
            {"id": "entry", "type": "entry", "calleeFullName": "Checkout.run"},
            {
                "id": "validate", "type": "call", "callerMethod": "Checkout.run",
                "calleeFullName": "Order.validate",
            },
            {
                "id": "reserve", "type": "call", "callerMethod": "Checkout.run",
                "calleeFullName": "Stock.reserve",
            },
            {
                "id": "notify", "type": "call", "callerMethod": "Checkout.run",
                "calleeFullName": "Mail.send",
            },
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


def _originals(flattened: Graph, node_ids: list[str]) -> list[str]:
    nodes = {node.id: node for node in flattened.nodes}
    return [nodes[node_id].origId or nodes[node_id].id for node_id in node_ids]


def test_orchestration_resolves_before_overlay_and_labelling() -> None:
    events = []

    def resolve(_graph, questions):
        events.append(("gate", tuple(question.gate.candidateId for question in questions)))
        return {
            question.id: (
                ("MERGE", 0.9, ("llm:checkout preparation",))
                if question.gate.candidateId == "reserve"
                else ("SPLIT", 0.9, ("llm:next subprocess",))
            )
            for question in questions
        }

    def label(_graph, node_ids, index):
        events.append(("label", node_ids, index))
        return "Order Preparation" if index == 0 else "Customer Notification"

    graph = _graph()
    analysis = analyse_codebase_phases(graph, resolve)
    flattened = flatten_cfg(graph)
    result = discover_phases(analysis, flattened, label)

    assert [
        _originals(flattened, phase["nodes"])
        for phase in result["phases"]
    ] == [["validate", "reserve"], ["notify"]]
    assert [phase["label"] for phase in result["phases"]] == [
        "Order Preparation", "Customer Notification"
    ]
    assert [event[0] for event in events] == ["gate", "label", "label"]


def test_unanswered_llm_gate_remains_a_fallback_split_and_is_exported() -> None:
    graph = _graph()
    analysis = analyse_codebase_phases(graph, lambda _graph, _questions: {})
    flattened = flatten_cfg(graph)

    result = discover_phases(analysis, flattened)

    assert [_originals(flattened, phase["nodes"]) for phase in result["phases"]] == [
        ["validate"], ["reserve"], ["notify"]
    ]


def test_stage_one_exclusion_is_applied_before_overlay() -> None:
    graph = Graph.from_dict({
        "entryPoint": "run",
        "roots": ["entry"],
        "nodes": [
            {"id": "entry", "type": "entry", "calleeFullName": "run"},
            {
                "id": "work", "type": "call", "callerMethod": "run",
                "calleeFullName": "Order.save",
            },
            {
                "id": "error", "type": "call", "callerMethod": "run",
                "deadEnd": True,
                "calleeFullName": "IllegalStateException.<init>:void()",
                "branchArms": [{"groupId": "guard", "armLabel": "if"}],
            },
        ],
        "edges": [
            {"from": "entry", "to": "work", "type": "sequence"},
            {"from": "work", "to": "error", "type": "sequence"},
        ],
        "branchGroups": [{
            "id": "guard", "kind": "IF", "method": "run",
            "branchPointIds": ["work"],
            "arms": [{
                "label": "if", "empty": False, "terminus": "throw",
                "firstCallId": "error",
            }],
        }],
        "semanticFeatures": {"work": {"methodTerms": ["save", "order"]}},
    })
    analysis = analyse_codebase_phases(graph)
    flattened = flatten_cfg(graph)

    result = discover_phases(analysis, flattened)

    assert analysis.excluded == {"error": "exception-mechanic"}
    assert [_originals(flattened, phase["nodes"]) for phase in result["phases"]] == [
        ["work"]
    ]
