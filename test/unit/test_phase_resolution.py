from __future__ import annotations

import sys
from pathlib import Path

FLOWMAP_SRC = Path(__file__).resolve().parents[2] / "backend" / "src" / "flowmap"
sys.path.insert(0, str(FLOWMAP_SRC))

from domain.phase_resolution import (  # noqa: E402
    collect_uncertain_gates,
    resolve_uncertain_gates,
)
from domain.phase_segmentation import analyse  # noqa: E402
from model import Graph  # noqa: E402


def _uncertain_features(term: str) -> dict:
    return {
        "methodTerms": [term],
        "observedFeatures": ["methodTerms"],
    }


def _two_method_graph() -> Graph:
    return Graph.from_dict({
        "roots": ["first_entry", "second_entry"],
        "nodes": [
            {"id": "first_entry", "type": "entry", "calleeFullName": "first"},
            {"id": "a", "type": "call", "callerMethod": "first"},
            {"id": "b", "type": "call", "callerMethod": "first"},
            {"id": "second_entry", "type": "entry", "calleeFullName": "second"},
            {"id": "c", "type": "call", "callerMethod": "second"},
            {"id": "d", "type": "call", "callerMethod": "second"},
        ],
        "edges": [
            {"from": "first_entry", "to": "a", "type": "sequence"},
            {"from": "a", "to": "b", "type": "sequence"},
            {"from": "second_entry", "to": "c", "type": "sequence"},
            {"from": "c", "to": "d", "type": "sequence"},
        ],
        "semanticFeatures": {
            node_id: _uncertain_features(node_id)
            for node_id in ("a", "b", "c", "d")
        },
    })


def test_collects_uncertain_gates_across_all_methods() -> None:
    analysis = analyse(_two_method_graph())

    questions = collect_uncertain_gates(analysis)

    assert [question.id for question in questions] == ["q-1", "q-2"]
    assert [question.methodEntryId for question in questions] == [
        "first_entry", "second_entry"
    ]
    assert [question.currentPhaseNodeIds for question in questions] == [
        ("a",), ("c",)
    ]


def test_one_codebase_batch_updates_gates_and_rematerialises_methods() -> None:
    analysis = analyse(_two_method_graph())
    batches = []

    def resolver(graph, questions):
        batches.append(tuple(
            (question.methodEntryId, question.gate.candidateId)
            for question in questions
        ))
        return {
            questions[0].id: ("MERGE", 0.9, ("llm:first",)),
            questions[1].id: ("SPLIT", 0.8, ("llm:second",)),
        }

    resolved = resolve_uncertain_gates(analysis, resolver)

    assert resolved == 2
    assert batches == [(('first_entry', 'b'), ('second_entry', 'd'))]
    assert [phase.nodes for phase in analysis.methods["first_entry"].phases] == [
        ["a", "b"]
    ]
    assert [phase.nodes for phase in analysis.methods["second_entry"].phases] == [
        ["c"], ["d"]
    ]
    assert analysis.methods["first_entry"].gates[0].decidedBy == "llm"
    assert analysis.methods["second_entry"].gates[0].decidedBy == "llm"


def test_global_work_list_is_split_into_batches() -> None:
    node_ids = [f"n{index}" for index in range(6)]
    graph = Graph.from_dict({
        "roots": ["entry"],
        "nodes": [
            {"id": "entry", "type": "entry", "calleeFullName": "run"},
            *[
                {"id": node_id, "type": "call", "callerMethod": "run"}
                for node_id in node_ids
            ],
        ],
        "edges": [
            {"from": "entry", "to": node_ids[0], "type": "sequence"},
            *[
                {"from": left, "to": right, "type": "sequence"}
                for left, right in zip(node_ids, node_ids[1:])
            ],
        ],
        "semanticFeatures": {
            node_id: _uncertain_features(node_id) for node_id in node_ids
        },
    })
    analysis = analyse(graph)
    batch_sizes = []

    def resolver(subject, questions):
        batch_sizes.append(len(questions))
        return {
            question.id: ("MERGE", 0.9, ("llm:same process",))
            for question in questions
        }

    assert resolve_uncertain_gates(analysis, resolver, batch_size=2) == 5
    assert batch_sizes == [2, 2, 1]
    assert [phase.nodes for phase in analysis.methods["entry"].phases] == [
        node_ids
    ]


def test_missing_or_invalid_answers_leave_fallback_boundaries() -> None:
    analysis = analyse(_two_method_graph())

    resolved = resolve_uncertain_gates(
        analysis,
        lambda graph, questions: {
            questions[0].id: ("INVALID", 0.9, ()),
        },
    )

    assert resolved == 0
    assert all(
        gate.action == "UNCERTAIN" and gate.decidedBy == "fallback"
        for method in analysis.methods.values()
        for gate in method.gates
        if gate.kind == "adjacency"
    )


def test_non_positive_batch_size_is_rejected() -> None:
    analysis = analyse(_two_method_graph())

    try:
        resolve_uncertain_gates(analysis, lambda *_: {}, batch_size=0)
    except ValueError as error:
        assert "positive" in str(error)
    else:
        raise AssertionError("expected invalid batch size to fail")


def test_region_override_suppresses_uncertain_gate_inside_collapsed_region() -> None:
    graph = Graph.from_dict({
        "roots": ["entry"],
        "nodes": [
            {"id": "entry", "type": "entry", "calleeFullName": "run"},
            {"id": "pre", "type": "call", "callerMethod": "run"},
            {
                "id": "a", "type": "call", "callerMethod": "run",
                "branchArms": [{"groupId": "g", "armLabel": "if"}],
            },
            {
                "id": "b", "type": "call", "callerMethod": "run",
                "branchArms": [{"groupId": "g", "armLabel": "if"}],
            },
        ],
        "edges": [
            {"from": "entry", "to": "pre", "type": "sequence"},
            {"from": "pre", "to": "a", "type": "sequence"},
            {"from": "a", "to": "b", "type": "sequence"},
        ],
        "semanticFeatures": {
            node_id: {
                "methodTerms": ["order"],
                "observedFeatures": ["methodTerms"],
            }
            for node_id in ("pre", "a", "b")
        },
    })
    analysis = analyse(graph)

    assert any(
        gate.kind == "adjacency" and gate.action == "UNCERTAIN"
        for gate in analysis.methods["entry"].gates
    )
    assert [phase.nodes for phase in analysis.methods["entry"].phases] == [
        ["pre", "a", "b"]
    ]
    assert collect_uncertain_gates(analysis) == ()
