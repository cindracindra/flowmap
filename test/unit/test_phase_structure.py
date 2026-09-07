from __future__ import annotations

import sys
from pathlib import Path

FLOWMAP_SRC = Path(__file__).resolve().parents[2] / "backend" / "src" / "flowmap"
sys.path.insert(0, str(FLOWMAP_SRC))

from domain.execution_phase.structure import (  # noqa: E402
    BranchStructure,
    LinearStructure,
    MethodStructure,
    build_method_structures as build_structures_from_definitions,
)
from domain.method_scoping import build_method_definitions  # noqa: E402
from model import Graph  # noqa: E402


def build_method_structures(
    graph: Graph,
    excluded=None,
    method_definitions=None,
):
    definitions = method_definitions or build_method_definitions(graph)
    return build_structures_from_definitions(definitions, excluded)


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

    expected = MethodStructure(
        "entry",
        (
            LinearStructure(("pre",)),
            BranchStructure(
                "outer",
                (
                    (
                        LinearStructure(("outer_a",)),
                        BranchStructure(
                            "inner",
                            (
                                (LinearStructure(("inner_a",)),),
                                (LinearStructure(("inner_b",)),),
                            ),
                        ),
                    ),
                    (LinearStructure(("outer_b",)),),
                ),
            ),
            LinearStructure(("join", "after")),
        ),
    )
    assert method == expected


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

    assert structures == {
        "caller": MethodStructure("caller", (LinearStructure(("call",)),)),
        "callee": MethodStructure("callee", (LinearStructure(("inside",)),)),
    }


def test_prebuilt_method_definitions_preserve_structure_output() -> None:
    graph = _nested_branch_graph()

    legacy_call = build_method_structures(graph)
    canonical_call = build_method_structures(
        graph,
        method_definitions=build_method_definitions(graph),
    )

    assert canonical_call == legacy_call


def test_structural_branch_nodes_do_not_enter_or_split_phase_structures() -> None:
    graph = Graph.from_dict({
        "nodes": [
            {"id": "entry", "type": "entry", "calleeFullName": "run"},
            {"id": "before", "type": "call", "callerMethod": "run"},
            {
                "id": "decision", "type": "structure", "callerMethod": "run",
                "structureGroupId": "g1", "structureRole": "decision", "code": "enabled",
            },
            {
                "id": "work", "type": "call", "callerMethod": "run",
                "branchArms": [{"groupId": "g1", "armLabel": "if"}],
            },
            {"id": "after", "type": "call", "callerMethod": "run"},
        ],
        "edges": [
            {"from": "entry", "to": "before", "type": "sequence"},
            {"from": "before", "to": "decision", "type": "sequence"},
            {"from": "decision", "to": "work", "type": "sequence"},
            {"from": "work", "to": "after", "type": "sequence"},
        ],
        "branchGroups": [{
            "id": "g1", "kind": "IF", "method": "run", "entryNodeId": "decision",
            "arms": [
                {"label": "if", "empty": False, "exits": [{"kind": "continues"}]},
                {"label": "else", "empty": True, "exits": [{"kind": "continues"}]},
            ],
        }],
    })

    structure = build_method_structures(graph)["entry"]

    assert structure == MethodStructure(
        "entry",
        (
            LinearStructure(("before",)),
            BranchStructure("g1", ((LinearStructure(("work",)),),)),
            LinearStructure(("after",)),
        ),
    )


def test_loop_anchors_and_transfer_do_not_split_call_sequence() -> None:
    graph = Graph.from_dict({
        "nodes": [
            {"id": "entry", "type": "entry", "calleeFullName": "run"},
            {"id": "before", "type": "call", "callerMethod": "run"},
            {"id": "loop_entry", "type": "structure", "callerMethod": "run", "structureGroupId": "loop", "structureRole": "entry"},
            {"id": "body", "type": "call", "callerMethod": "run", "loopIds": ["loop"]},
            {"id": "continue", "type": "transfer", "callerMethod": "run", "transferKind": "continue", "targetStructureGroupId": "loop", "loopIds": ["loop"]},
            {"id": "loop_exit", "type": "structure", "callerMethod": "run", "structureGroupId": "loop", "structureRole": "exit"},
            {"id": "after", "type": "call", "callerMethod": "run"},
        ],
        "edges": [
            {"from": "entry", "to": "before", "type": "sequence"},
            {"from": "before", "to": "loop_entry", "type": "sequence"},
            {"from": "loop_entry", "to": "body", "type": "sequence"},
            {"from": "body", "to": "continue", "type": "sequence"},
            {"from": "continue", "to": "loop_exit", "type": "sequence"},
            {"from": "loop_exit", "to": "after", "type": "sequence"},
        ],
        "loopGroups": [{
            "id": "loop", "kind": "WHILE", "method": "run",
            "entryNodeId": "loop_entry", "exitNodeId": "loop_exit",
        }],
    })

    structure = build_method_structures(graph)["entry"]

    assert structure.structures == (
        LinearStructure(("before", "body", "after")),
    )


def test_explicitly_owned_disconnected_call_is_not_dropped() -> None:
    graph = Graph.from_dict({
        "nodes": [
            {"id": "entry", "type": "entry", "calleeFullName": "run"},
            {"id": "connected", "type": "call", "callerMethod": "run"},
            {"id": "detached", "type": "call", "callerMethod": "run"},
        ],
        "edges": [
            {"from": "entry", "to": "connected", "type": "sequence"},
        ],
    })

    method = build_method_structures(graph)["entry"]

    assert method.structures == (
        LinearStructure(("connected",)),
        LinearStructure(("detached",)),
    )


def test_branch_arm_order_follows_cfg_edges_not_node_serialization() -> None:
    graph = Graph.from_dict({
        "nodes": [
            {"id": "entry", "type": "entry", "calleeFullName": "run"},
            {"id": "pre", "type": "call", "callerMethod": "run"},
            {
                "id": "serialized_first", "type": "call", "callerMethod": "run",
                "branchArms": [{"groupId": "choice", "armLabel": "else"}],
            },
            {
                "id": "cfg_first", "type": "call", "callerMethod": "run",
                "branchArms": [{"groupId": "choice", "armLabel": "if"}],
            },
            {"id": "after", "type": "call", "callerMethod": "run"},
        ],
        "edges": [
            {"from": "entry", "to": "pre", "type": "sequence"},
            {"from": "pre", "to": "cfg_first", "type": "sequence"},
            {"from": "pre", "to": "serialized_first", "type": "sequence"},
            {"from": "cfg_first", "to": "after", "type": "sequence"},
            {"from": "serialized_first", "to": "after", "type": "sequence"},
        ],
    })

    method = build_method_structures(graph)["entry"]
    branch = method.structures[1]

    assert isinstance(branch, BranchStructure)
    assert branch.arms == (
        (LinearStructure(("cfg_first",)),),
        (LinearStructure(("serialized_first",)),),
    )
