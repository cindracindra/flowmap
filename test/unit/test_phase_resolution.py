from __future__ import annotations

import sys
from pathlib import Path

FLOWMAP_SRC = Path(__file__).resolve().parents[2] / "backend" / "src" / "flowmap"
sys.path.insert(0, str(FLOWMAP_SRC))

from domain.phase_resolution import (  # noqa: E402
    construct_connected_candidates,
    refine_ambiguous_gates,
)
from model import Graph  # noqa: E402


OBSERVED = ["receiver", "arguments", "inputs", "callsiteFields", "output"]


def _feature(*, receiver=None, inputs=(), role=None):
    value = {
        "inputIdentifiers": list(inputs),
        "arguments": [],
        "fieldsRead": [],
        "fieldsWritten": [],
        "domainTypes": [],
        "methodTerms": [],
        "observedFeatures": OBSERVED,
    }
    if receiver is not None:
        value["receiver"] = receiver
    if role is not None:
        value["role"] = role
    return value


def _graph(features, sequence):
    return Graph.from_dict({
        "nodes": [
            {"id": node_id, "type": "call", "calleeFullName": f"Service.{node_id}"}
            for node_id in features
        ],
        "edges": [
            {"from": source, "to": target, "type": "sequence"}
            for source, target in sequence
        ],
        "semanticFeatures": features,
    })


def test_stage_8_constructs_local_first_candidates() -> None:
    subject = _graph(
        {
            "a": _feature(receiver="account", inputs=("order",)),
            "b": _feature(receiver="account", inputs=("order",)),
            "c": _feature(receiver="mailer", inputs=("message",)),
        },
        (("a", "b"), ("b", "c")),
    )

    plan = construct_connected_candidates(subject, ("a", "b", "c"))

    assert [phase.nodeIds for phase in plan.phases] == [("a", "b"), ("c",)]
    assert [gate.action for gate in plan.gates] == ["MERGE", "SPLIT"]


def test_stage_8_excludes_exception_mechanics() -> None:
    subject = _graph(
        {
            "a": _feature(receiver="account", inputs=("order",)),
            "throw": _feature(role="exception-mechanic"),
        },
        (("a", "throw"),),
    )

    plan = construct_connected_candidates(subject, ("a", "throw"))

    assert plan.orderedNodeIds == ("a",)
    assert plan.phases[0].nodeIds == ("a",)


def test_stage_9_calls_llm_only_for_uncertain_gates() -> None:
    subject = _graph(
        {
            "a": _feature(receiver="account", inputs=("order",)),
            "b": _feature(receiver="account", inputs=("order",)),
            # Missing observations make b -> c uncertain.
            "c": {
                "methodTerms": ["validate", "order"],
                "observedFeatures": ["methodTerms"],
            },
        },
        (("a", "b"), ("b", "c")),
    )
    plan = construct_connected_candidates(subject, ("a", "b", "c"))
    calls = []

    def resolver(graph, gates, phase_nodes):
        calls.append((tuple((g.frontierId, g.candidateId) for g in gates), phase_nodes))
        return {
            (gate.frontierId, gate.candidateId): (
                "MERGE", 0.8, ("llm:same validation subprocess",)
            )
            for gate in gates if gate.action == "UNCERTAIN"
        }

    refined = refine_ambiguous_gates(subject, plan, resolver)

    assert calls == [((("a", "b"), ("b", "c")), ("a",))]
    assert refined.phases[0].nodeIds == ("a", "b", "c")
    assert refined.gates[0].decidedBy == "systematic"
    assert refined.gates[1].decidedBy == "llm"


def test_invalid_llm_answer_leaves_uncertainty_visible() -> None:
    subject = _graph(
        {"a": _feature(receiver="this"), "b": _feature(receiver="this")},
        (("a", "b"),),
    )
    plan = construct_connected_candidates(subject, ("a", "b"))

    refined = refine_ambiguous_gates(subject, plan, lambda *_: {})

    assert refined.gates[0].action == "UNCERTAIN"
    assert refined.gates[0].decidedBy == "systematic"
    assert [phase.nodeIds for phase in refined.phases] == [("a",), ("b",)]


def test_ambiguous_gates_are_batched_twenty_per_query() -> None:
    node_ids = tuple(f"n{index}" for index in range(42))
    subject = _graph(
        {node_id: _feature(receiver="this") for node_id in node_ids},
        tuple(zip(node_ids, node_ids[1:])),
    )
    plan = construct_connected_candidates(subject, node_ids)
    batch_sizes = []

    def resolver(graph, gates, phase_nodes):
        unknown = [gate for gate in gates if gate.action == "UNCERTAIN"]
        batch_sizes.append(len(unknown))
        return {
            (gate.frontierId, gate.candidateId): (
                "MERGE", 0.9, ("llm:same subprocess",)
            )
            for gate in unknown
        }

    refined = refine_ambiguous_gates(subject, plan, resolver)

    assert batch_sizes == [20, 20, 1]
    assert len(refined.phases) == 1
