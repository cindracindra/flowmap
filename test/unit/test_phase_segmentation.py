from __future__ import annotations

import sys
from pathlib import Path

FLOWMAP_SRC = Path(__file__).resolve().parents[2] / "backend" / "src" / "flowmap"
sys.path.insert(0, str(FLOWMAP_SRC))

from domain.phase_segmentation import (  # noqa: E402
    GateOverride,
    RetainedCall,
    Segment,
    analyse,
    materialise,
    phase_count,
    systematic_action,
)
from model import Graph  # noqa: E402


def call(node_id: str, method: str = "run", **features) -> dict:
    node = {
        "id": node_id, "type": "call", "calleeFullName": f"S.{node_id}",
        "callerMethod": method,
    }
    node.update({k: v for k, v in features.items() if k == "branchArms"})
    return node


def features(receiver=None, inputs=(), arguments=()) -> dict:
    result = {
        "inputIdentifiers": list(inputs),
        "arguments": list(arguments),
        "observedFeatures": ["receiver", "arguments", "inputs"],
    }
    if receiver is not None:
        result["receiver"] = receiver
    return result


def items(analysis, entry_id):
    return [
        tuple(item.nodeIds) if isinstance(item, Segment) else item.callSiteId
        for item in materialise(analysis, entry_id)
    ]


def test_related_neighbours_merge_into_one_segment() -> None:
    graph = Graph.from_dict({
        "nodes": [
            {"id": "e", "type": "entry", "calleeFullName": "run"},
            call("a"), call("b"),
        ],
        "edges": [
            {"from": "e", "to": "a", "type": "sequence"},
            {"from": "a", "to": "b", "type": "sequence"},
        ],
        "roots": ["e"],
        "semanticFeatures": {
            "a": features(receiver="cart", inputs=("order",)),
            "b": features(receiver="cart", inputs=("order",)),
        },
    })

    analysis = analyse(graph)

    assert items(analysis, "e") == [("a", "b")]
    assert phase_count(analysis, "e") == 1


def test_disjoint_neighbours_split() -> None:
    graph = Graph.from_dict({
        "nodes": [
            {"id": "e", "type": "entry", "calleeFullName": "run"},
            call("a"), call("b"),
        ],
        "edges": [
            {"from": "e", "to": "a", "type": "sequence"},
            {"from": "a", "to": "b", "type": "sequence"},
        ],
        "roots": ["e"],
        "semanticFeatures": {
            "a": features(receiver="cart", inputs=("order",), arguments=("order",)),
            "b": features(receiver="mailer", inputs=("template",), arguments=("template",)),
        },
    })

    analysis = analyse(graph)

    assert items(analysis, "e") == [("a",), ("b",)]
    assert phase_count(analysis, "e") == 2


def _nested(callee_calls: int) -> Graph:
    """run(): a(); mid(); with mid() holding `callee_calls` unrelated operations."""
    nodes = [
        {"id": "e", "type": "entry", "calleeFullName": "run"},
        call("a"), call("mid"),
        {"id": "e2", "type": "entry", "calleeFullName": "mid"},
    ]
    edges = [
        {"from": "e", "to": "a", "type": "sequence"},
        {"from": "a", "to": "mid", "type": "sequence"},
        {"from": "mid", "to": "e2", "type": "invoke"},
    ]
    semantic = {
        "a": features(receiver="cart", inputs=("order",), arguments=("order",)),
        "mid": features(receiver="mailer", inputs=("template",), arguments=("template",)),
    }
    previous = "e2"
    for index in range(callee_calls):
        inner = f"i{index}"
        nodes.append(call(inner, method="mid"))
        edges.append({"from": previous, "to": inner, "type": "sequence"})
        semantic[inner] = features(
            receiver=f"r{index}", inputs=(f"v{index}",), arguments=(f"v{index}",)
        )
        previous = inner
    return Graph.from_dict({
        "nodes": nodes, "edges": edges, "roots": ["e"], "semanticFeatures": semantic,
    })


def test_a_callee_with_no_visible_work_is_atomic() -> None:
    analysis = analyse(_nested(0))

    assert phase_count(analysis, "e2") == 0
    assert items(analysis, "e") == [("a",), ("mid",)]


def test_a_single_phase_callee_stays_an_ordinary_member() -> None:
    analysis = analyse(_nested(1))

    assert phase_count(analysis, "e2") == 1
    # `mid` is still a member of its caller's sequence, not a hole.
    assert items(analysis, "e") == [("a",), ("mid",)]


def test_a_multi_phase_callee_is_retained_as_a_hole() -> None:
    analysis = analyse(_nested(3))

    assert phase_count(analysis, "e2") == 3
    assert items(analysis, "e") == [("a",), "mid"]
    # The caller's own phase plus the three the callee contributes.
    assert phase_count(analysis, "e") == 4


def test_the_retained_call_site_belongs_to_no_segment() -> None:
    analysis = analyse(_nested(3))

    members = {
        node_id
        for item in materialise(analysis, "e")
        if isinstance(item, Segment)
        for node_id in item.nodeIds
    }
    assert "mid" not in members


def test_callees_are_resolved_before_their_callers() -> None:
    analysis = analyse(_nested(3))

    assert set(analysis.segments) == {"e", "e2"}
    assert analysis.segments["e2"].sequence == ("i0", "i1", "i2")


def test_recursion_is_cut_off_rather_than_looping() -> None:
    graph = Graph.from_dict({
        "nodes": [
            {"id": "e", "type": "entry", "calleeFullName": "run"},
            call("a"), call("again"),
        ],
        "edges": [
            {"from": "e", "to": "a", "type": "sequence"},
            {"from": "a", "to": "again", "type": "sequence"},
            {"from": "again", "to": "e", "type": "invoke"},
        ],
        "roots": ["e"],
        "semanticFeatures": {"a": features(receiver="x"), "again": features(receiver="y")},
    })

    analysis = analyse(graph)

    assert analysis.segments["e"].sequence == ("a", "again")


def test_scope_boundaries_are_emitted_as_structural_gates() -> None:
    # a; if (…) { x }; the arm is a separate scope, so the boundary is structural.
    graph = Graph.from_dict({
        "nodes": [
            {"id": "e", "type": "entry", "calleeFullName": "run"},
            call("a"),
            {"id": "x", "type": "call", "calleeFullName": "S.x", "callerMethod": "run",
             "branchArms": [{"groupId": "g", "armLabel": "if"}]},
        ],
        "edges": [
            {"from": "e", "to": "a", "type": "sequence"},
            {"from": "a", "to": "x", "type": "sequence"},
        ],
        "roots": ["e"],
        "branchGroups": [{
            "id": "g", "kind": "IF", "method": "run",
            "arms": [{"label": "if", "empty": False, "terminus": "continues"}],
        }],
        "semanticFeatures": {"a": features(receiver="cart"), "x": features(receiver="cart")},
    })

    analysis = analyse(graph)
    gate = analysis.segments["e"].gates[0]

    assert gate.kind == "branch-entry"
    assert gate.action == "SPLIT"
    assert gate.decidedBy == "structural"
    assert items(analysis, "e") == [("a",), ("x",)]


def test_excluded_operations_never_reach_a_segment() -> None:
    graph = Graph.from_dict({
        "nodes": [
            {"id": "e", "type": "entry", "calleeFullName": "run"},
            call("a"), call("boom"),
        ],
        "edges": [
            {"from": "e", "to": "a", "type": "sequence"},
            {"from": "a", "to": "boom", "type": "sequence"},
        ],
        "roots": ["e"],
        "semanticFeatures": {"a": features(receiver="cart"), "boom": features(receiver="cart")},
    })

    analysis = analyse(graph, {"boom": "exception-mechanic"})

    assert analysis.segments["e"].sequence == ("a",)
    assert items(analysis, "e") == [("a",)]


def test_an_llm_answer_changes_the_projection_without_touching_segments() -> None:
    import dataclasses

    analysis = analyse(_nested(3))
    inner = analysis.segments["e2"]
    assert phase_count(analysis, "e2") == 3

    merged = tuple(
        dataclasses.replace(
            gate,
            override=GateOverride("MERGE", "llm", 0.9, ("llm:same subprocess",)),
        )
        for gate in inner.gates
    )
    analysis.segments["e2"] = dataclasses.replace(inner, gates=merged)
    analysis.clear_counts()

    # The callee collapses to one phase, so its call site stops being a hole and
    # becomes an ordinary member of the caller -- with nothing recomputed.
    assert phase_count(analysis, "e2") == 1
    assert items(analysis, "e2") == [("i0", "i1", "i2")]
    assert items(analysis, "e") == [("a",), ("mid",)]


def test_systematic_action_table() -> None:
    from domain.phase_relationship import CohesionDecision, RelationshipDecision

    related = RelationshipDecision("RELATED", 0.9)
    unrelated = RelationshipDecision("UNRELATED", 0.8)
    unknown = RelationshipDecision("UNKNOWN", 0.6)
    compatible = CohesionDecision("COMPATIBLE", 0.85)
    incompatible = CohesionDecision("INCOMPATIBLE", 0.8)

    assert systematic_action(unrelated, compatible) == "SPLIT"
    assert systematic_action(related, compatible) == "MERGE"
    assert systematic_action(related, incompatible) == "UNCERTAIN"
    assert systematic_action(unknown, compatible) == "UNCERTAIN"
    assert systematic_action(None, None) == "SPLIT"
