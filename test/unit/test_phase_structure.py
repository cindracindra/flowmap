from __future__ import annotations

import sys
from pathlib import Path

FLOWMAP_SRC = Path(__file__).resolve().parents[2] / "backend" / "src" / "flowmap"
sys.path.insert(0, str(FLOWMAP_SRC))

from domain.phase_structure import (  # noqa: E402
    BranchStructure,
    LinearStructure,
    build_method_structures,
)
from model import Graph  # noqa: E402


def _nested_branch_graph() -> Graph:
    return Graph.from_dict({
        "nodes": [
            {"id": "entry", "type": "entry", "calleeFullName": "run"},
            {"id": "pre", "type": "call", "callerMethod": "run"},
            {
                "id": "outer_a", "type": "call", "callerMethod": "run",
                "branchArms": [{"groupId": "outer", "armLabel": "if"}],
            },
            {
                "id": "inner_a", "type": "call", "callerMethod": "run",
                "branchArms": [
                    {"groupId": "outer", "armLabel": "if"},
                    {"groupId": "inner", "armLabel": "if"},
                ],
            },
            {
                "id": "inner_b", "type": "call", "callerMethod": "run",
                "branchArms": [
                    {"groupId": "outer", "armLabel": "if"},
                    {"groupId": "inner", "armLabel": "else"},
                ],
            },
            {
                "id": "outer_b", "type": "call", "callerMethod": "run",
                "branchArms": [{"groupId": "outer", "armLabel": "else"}],
            },
            {"id": "join", "type": "call", "callerMethod": "run"},
            {"id": "after", "type": "call", "callerMethod": "run"},
        ],
        "edges": [
            {"from": "entry", "to": "pre", "type": "sequence"},
            {"from": "pre", "to": "outer_a", "type": "sequence"},
            {"from": "pre", "to": "outer_b", "type": "sequence"},
            {"from": "outer_a", "to": "inner_a", "type": "sequence"},
            {"from": "outer_a", "to": "inner_b", "type": "sequence"},
            {"from": "inner_a", "to": "join", "type": "sequence"},
            {"from": "inner_b", "to": "join", "type": "sequence"},
            {"from": "outer_b", "to": "join", "type": "sequence"},
            {"from": "join", "to": "after", "type": "sequence"},
        ],
    })


def test_builds_nested_branch_structures_from_node_tags() -> None:
    method = build_method_structures(_nested_branch_graph())["entry"]

    assert method.structures[0] == LinearStructure(("pre",))
    outer = method.structures[1]
    assert isinstance(outer, BranchStructure)
    assert outer.groupId == "outer"
    assert len(outer.arms) == 2

    assert outer.arms[0][0] == LinearStructure(("outer_a",))
    inner = outer.arms[0][1]
    assert isinstance(inner, BranchStructure)
    assert inner.groupId == "inner"
    assert inner.arms == (
        (LinearStructure(("inner_a",)),),
        (LinearStructure(("inner_b",)),),
    )
    assert outer.arms[1] == (LinearStructure(("outer_b",)),)
    assert method.structures[2] == LinearStructure(("join", "after"))


def test_excluded_nodes_are_absent_but_still_split_the_structure() -> None:
    graph = Graph.from_dict({
        "nodes": [
            {"id": "entry", "type": "entry", "calleeFullName": "run"},
            {"id": "check", "type": "call", "callerMethod": "run"},
            {
                "id": "exception", "type": "call", "callerMethod": "run",
                "deadEnd": True,
                "branchArms": [{"groupId": "guard", "armLabel": "if"}],
            },
            {"id": "continue", "type": "call", "callerMethod": "run"},
        ],
        "edges": [
            {"from": "entry", "to": "check", "type": "sequence"},
            {"from": "check", "to": "exception", "type": "sequence"},
            {"from": "check", "to": "continue", "type": "sequence"},
        ],
    })

    method = build_method_structures(
        graph, {"exception": "exception-mechanic"}
    )["entry"]

    assert method.structures == (
        LinearStructure(("check",)),
        LinearStructure(("continue",)),
    )


def test_loop_body_remains_an_ordinary_linear_structure() -> None:
    graph = Graph.from_dict({
        "nodes": [
            {"id": "entry", "type": "entry", "calleeFullName": "run"},
            {"id": "pre", "type": "call", "callerMethod": "run"},
            {"id": "head", "type": "call", "callerMethod": "run", "loopIds": ["L"]},
            {"id": "body", "type": "call", "callerMethod": "run", "loopIds": ["L"]},
            {"id": "tail", "type": "call", "callerMethod": "run", "loopIds": ["L"]},
            {"id": "after", "type": "call", "callerMethod": "run"},
        ],
        "edges": [
            {"from": "entry", "to": "pre", "type": "sequence"},
            {"from": "pre", "to": "head", "type": "sequence"},
            {"from": "head", "to": "body", "type": "sequence"},
            {"from": "body", "to": "tail", "type": "sequence"},
            {"from": "tail", "to": "head", "type": "sequence"},
            {"from": "tail", "to": "after", "type": "sequence"},
        ],
    })

    method = build_method_structures(graph)["entry"]

    assert method.structures == (
        LinearStructure(("pre",)),
        LinearStructure(("head", "body", "tail")),
        LinearStructure(("after",)),
    )


def test_methods_are_kept_separate() -> None:
    graph = Graph.from_dict({
        "nodes": [
            {"id": "caller", "type": "entry", "calleeFullName": "run"},
            {"id": "call", "type": "call", "callerMethod": "run"},
            {"id": "callee", "type": "entry", "calleeFullName": "helper"},
            {"id": "inside", "type": "call", "callerMethod": "helper"},
        ],
        "edges": [
            {"from": "caller", "to": "call", "type": "sequence"},
            {"from": "call", "to": "callee", "type": "invoke"},
            {"from": "callee", "to": "inside", "type": "sequence"},
        ],
    })

    structures = build_method_structures(graph)

    assert structures["caller"].structures == (LinearStructure(("call",)),)
    assert structures["callee"].structures == (LinearStructure(("inside",)),)
