from __future__ import annotations

import sys
from pathlib import Path

FLOWMAP_SRC = Path(__file__).resolve().parents[2] / "backend" / "src" / "flowmap"
sys.path.insert(0, str(FLOWMAP_SRC))

from domain.phase_resolution import resolve_uncertain_gates  # noqa: E402
from domain.phase_retention import recheck_lapsed_retentions  # noqa: E402
from domain.phase_segmentation import analyse, phase_count  # noqa: E402
from model import Graph  # noqa: E402


def _features(identity: str) -> dict:
    return {
        "receiver": identity,
        "inputIdentifiers": [identity],
        "arguments": [identity],
        "observedFeatures": ["receiver", "inputs", "arguments"],
    }


def _uncertain(term: str) -> dict:
    return {
        "methodTerms": [term],
        "observedFeatures": ["methodTerms"],
    }


def _resolve_all_as_merge(analysis) -> None:
    resolve_uncertain_gates(
        analysis,
        lambda graph, questions: {
            question.id: ("MERGE", 0.9, ("llm:same subprocess",))
            for question in questions
        },
    )


def test_lapsed_call_is_rechecked_against_both_neighboring_phases() -> None:
    graph = Graph.from_dict({
        "roots": ["caller_entry"],
        "nodes": [
            {"id": "caller_entry", "type": "entry", "calleeFullName": "run"},
            {"id": "pre", "type": "call", "callerMethod": "run"},
            {"id": "call_helper", "type": "call", "callerMethod": "run"},
            {"id": "after", "type": "call", "callerMethod": "run"},
            {"id": "helper_entry", "type": "entry", "calleeFullName": "helper"},
            {"id": "first", "type": "call", "callerMethod": "helper"},
            {"id": "second", "type": "call", "callerMethod": "helper"},
        ],
        "edges": [
            {"from": "caller_entry", "to": "pre", "type": "sequence"},
            {"from": "pre", "to": "call_helper", "type": "sequence"},
            {"from": "call_helper", "to": "after", "type": "sequence"},
            {"from": "call_helper", "to": "helper_entry", "type": "invoke"},
            {"from": "helper_entry", "to": "first", "type": "sequence"},
            {"from": "first", "to": "second", "type": "sequence"},
        ],
        "semanticFeatures": {
            "pre": _features("order"),
            "call_helper": _features("order"),
            "after": _features("order"),
            "first": _uncertain("prepare"),
            "second": _uncertain("commit"),
        },
    })
    analysis = analyse(graph)
    assert analysis.methods["caller_entry"].retainedCallIds == {"call_helper"}

    _resolve_all_as_merge(analysis)
    assert phase_count(analysis, "helper_entry") == 1
    assert recheck_lapsed_retentions(analysis) == 1

    caller = analysis.methods["caller_entry"]
    assert caller.retainedCallIds == set()
    assert [phase.nodes for phase in caller.phases] == [
        ["pre", "call_helper", "after"]
    ]
    assert [gate.kind for gate in caller.gates] == ["adjacency", "adjacency"]


def test_depth_first_recheck_propagates_lapsed_retention_to_root() -> None:
    graph = Graph.from_dict({
        "roots": ["a_entry"],
        "nodes": [
            {"id": "a_entry", "type": "entry", "calleeFullName": "a"},
            {"id": "call_b", "type": "call", "callerMethod": "a"},
            {"id": "b_entry", "type": "entry", "calleeFullName": "b"},
            {"id": "call_c", "type": "call", "callerMethod": "b"},
            {"id": "c_entry", "type": "entry", "calleeFullName": "c"},
            {"id": "c_first", "type": "call", "callerMethod": "c"},
            {"id": "c_second", "type": "call", "callerMethod": "c"},
        ],
        "edges": [
            {"from": "a_entry", "to": "call_b", "type": "sequence"},
            {"from": "call_b", "to": "b_entry", "type": "invoke"},
            {"from": "b_entry", "to": "call_c", "type": "sequence"},
            {"from": "call_c", "to": "c_entry", "type": "invoke"},
            {"from": "c_entry", "to": "c_first", "type": "sequence"},
            {"from": "c_first", "to": "c_second", "type": "sequence"},
        ],
        "semanticFeatures": {
            "call_b": _features("b"),
            "call_c": _features("c"),
            "c_first": _uncertain("first"),
            "c_second": _uncertain("second"),
        },
    })
    analysis = analyse(graph)
    assert analysis.methods["a_entry"].retainedCallIds == {"call_b"}
    assert analysis.methods["b_entry"].retainedCallIds == {"call_c"}

    _resolve_all_as_merge(analysis)
    assert recheck_lapsed_retentions(analysis) == 2

    assert analysis.methods["a_entry"].retainedCallIds == set()
    assert analysis.methods["b_entry"].retainedCallIds == set()
    assert [phase.nodes for phase in analysis.methods["a_entry"].phases] == [
        ["call_b"]
    ]
    assert [phase.nodes for phase in analysis.methods["b_entry"].phases] == [
        ["call_c"]
    ]


def test_retention_remains_when_callee_still_has_multiple_phases() -> None:
    graph = Graph.from_dict({
        "roots": ["caller_entry"],
        "nodes": [
            {"id": "caller_entry", "type": "entry", "calleeFullName": "run"},
            {"id": "call_helper", "type": "call", "callerMethod": "run"},
            {"id": "helper_entry", "type": "entry", "calleeFullName": "helper"},
            {"id": "first", "type": "call", "callerMethod": "helper"},
            {"id": "second", "type": "call", "callerMethod": "helper"},
        ],
        "edges": [
            {"from": "caller_entry", "to": "call_helper", "type": "sequence"},
            {"from": "call_helper", "to": "helper_entry", "type": "invoke"},
            {"from": "helper_entry", "to": "first", "type": "sequence"},
            {"from": "first", "to": "second", "type": "sequence"},
        ],
        "semanticFeatures": {
            "call_helper": _features("helper"),
            "first": _features("stock"),
            "second": _features("mailer"),
        },
    })
    analysis = analyse(graph)

    assert recheck_lapsed_retentions(analysis) == 0
    assert analysis.methods["caller_entry"].retainedCallIds == {"call_helper"}


def test_lapsed_retention_does_not_override_a_loop_structure_boundary() -> None:
    graph = Graph.from_dict({
        "roots": ["caller_entry"],
        "nodes": [
            {"id": "caller_entry", "type": "entry", "calleeFullName": "run"},
            {"id": "pre", "type": "call", "callerMethod": "run"},
            {"id": "call_helper", "type": "call", "callerMethod": "run", "loopIds": ["L"]},
            {"id": "body", "type": "call", "callerMethod": "run", "loopIds": ["L"]},
            {"id": "tail", "type": "call", "callerMethod": "run", "loopIds": ["L"]},
            {"id": "after", "type": "call", "callerMethod": "run"},
            {"id": "helper_entry", "type": "entry", "calleeFullName": "helper"},
            {"id": "first", "type": "call", "callerMethod": "helper"},
            {"id": "second", "type": "call", "callerMethod": "helper"},
        ],
        "edges": [
            {"from": "caller_entry", "to": "pre", "type": "sequence"},
            {"from": "pre", "to": "call_helper", "type": "sequence"},
            {"from": "call_helper", "to": "body", "type": "sequence"},
            {"from": "body", "to": "tail", "type": "sequence"},
            {"from": "tail", "to": "call_helper", "type": "sequence"},
            {"from": "tail", "to": "after", "type": "sequence"},
            {"from": "call_helper", "to": "helper_entry", "type": "invoke"},
            {"from": "helper_entry", "to": "first", "type": "sequence"},
            {"from": "first", "to": "second", "type": "sequence"},
        ],
        "semanticFeatures": {
            node_id: _features("order")
            for node_id in ("pre", "call_helper", "body", "tail", "after")
        } | {
            "first": _uncertain("first"),
            "second": _uncertain("second"),
        },
    })
    analysis = analyse(graph)

    _resolve_all_as_merge(analysis)
    assert recheck_lapsed_retentions(analysis) == 1

    method = analysis.methods["caller_entry"]
    assert [phase.nodes for phase in method.phases] == [
        ["pre"], ["call_helper", "body", "tail"], ["after"]
    ]
    assert any(
        gate.kind == "region-split"
        and gate.frontierId == "pre"
        and gate.candidateId == "call_helper"
        for gate in method.gates
    )


def test_lapsed_retention_preserves_branch_convergence_boundary() -> None:
    graph = Graph.from_dict({
        "roots": ["caller_entry"],
        "nodes": [
            {"id": "caller_entry", "type": "entry", "calleeFullName": "run"},
            {"id": "guard", "type": "call", "callerMethod": "run"},
            {
                "id": "arm", "type": "call", "callerMethod": "run",
                "branchArms": [{"groupId": "g", "armLabel": "if"}],
            },
            {"id": "call_helper", "type": "call", "callerMethod": "run"},
            {"id": "helper_entry", "type": "entry", "calleeFullName": "helper"},
            {"id": "first", "type": "call", "callerMethod": "helper"},
            {"id": "second", "type": "call", "callerMethod": "helper"},
        ],
        "edges": [
            {"from": "caller_entry", "to": "guard", "type": "sequence"},
            {"from": "guard", "to": "arm", "type": "sequence"},
            {"from": "guard", "to": "call_helper", "type": "sequence"},
            {"from": "arm", "to": "call_helper", "type": "sequence"},
            {"from": "call_helper", "to": "helper_entry", "type": "invoke"},
            {"from": "helper_entry", "to": "first", "type": "sequence"},
            {"from": "first", "to": "second", "type": "sequence"},
        ],
        "semanticFeatures": {
            "guard": _features("order"),
            "arm": _features("stock"),
            "call_helper": _features("order"),
            "first": _uncertain("first"),
            "second": _uncertain("second"),
        },
    })
    analysis = analyse(graph)

    _resolve_all_as_merge(analysis)
    assert recheck_lapsed_retentions(analysis) == 1

    method = analysis.methods["caller_entry"]
    assert [phase.nodes for phase in method.phases] == [
        ["guard"], ["arm"], ["call_helper"]
    ]
    assert any(
        gate.kind == "branch-convergence"
        and gate.frontierId == "g"
        and gate.candidateId == "call_helper"
        for gate in method.gates
    )
