from __future__ import annotations

import sys
from pathlib import Path

FLOWMAP_SRC = Path(__file__).resolve().parents[2] / "backend" / "src" / "flowmap"
sys.path.insert(0, str(FLOWMAP_SRC))

from domain.phase_structure import build_structural_scopes, classify_operation_roles
from model import Graph


def _nested_branch_graph() -> Graph:
    return Graph.from_dict(
        {
            "entryPoint": "run",
            "rootId": "entry",
            "nodes": [
                {"id": "entry", "type": "entry", "calleeFullName": "run"},
                {"id": "pre", "type": "call", "calleeFullName": "Service.prepare", "callerMethod": "run"},
                {
                    "id": "outer_a", "type": "call", "calleeFullName": "Service.pathA", "callerMethod": "run",
                    "branchArms": [{"groupId": "outer", "armLabel": "if"}],
                },
                {
                    "id": "outer_b", "type": "call", "calleeFullName": "Service.pathB", "callerMethod": "run",
                    "branchArms": [{"groupId": "outer", "armLabel": "else"}],
                },
                {
                    "id": "inner_a", "type": "call", "calleeFullName": "Service.innerA", "callerMethod": "run",
                    "branchArms": [
                        {"groupId": "outer", "armLabel": "if"},
                        {"groupId": "inner", "armLabel": "if"},
                    ],
                },
                {
                    "id": "inner_b", "type": "call", "calleeFullName": "Service.innerB", "callerMethod": "run",
                    "branchArms": [
                        {"groupId": "outer", "armLabel": "if"},
                        {"groupId": "inner", "armLabel": "else"},
                    ],
                },
                {"id": "join", "type": "call", "calleeFullName": "Service.finish", "callerMethod": "run"},
                {"id": "after", "type": "call", "calleeFullName": "Audit.record", "callerMethod": "run"},
            ],
            "edges": [
                {"from": "entry", "to": "pre", "type": "sequence"},
                {"from": "pre", "to": "outer_a", "type": "sequence"},
                {"from": "pre", "to": "outer_b", "type": "sequence"},
                {"from": "outer_a", "to": "inner_a", "type": "sequence"},
                {"from": "outer_a", "to": "inner_b", "type": "sequence"},
                {"from": "outer_b", "to": "join", "type": "sequence"},
                {"from": "inner_a", "to": "join", "type": "sequence"},
                {"from": "inner_b", "to": "join", "type": "sequence"},
                {"from": "join", "to": "after", "type": "sequence"},
            ],
            "branchGroups": [
                {
                    "id": "outer", "kind": "IF", "method": "run", "line": 10,
                    "branchPointIds": ["pre"], "convergesAt": "join",
                    "arms": [
                        {"label": "if", "empty": False, "terminus": "continues", "firstCallId": "outer_a"},
                        {"label": "else", "empty": False, "terminus": "continues", "firstCallId": "outer_b"},
                    ],
                },
                {
                    "id": "inner", "kind": "IF", "method": "run", "line": 12,
                    "branchPointIds": ["outer_a"], "convergesAt": "join",
                    "arms": [
                        {"label": "if", "empty": False, "terminus": "continues", "firstCallId": "inner_a"},
                        {"label": "else", "empty": False, "terminus": "continues", "firstCallId": "inner_b"},
                    ],
                },
            ],
        }
    )


def test_structural_scopes_build_nested_branch_regions_with_virtual_anchors() -> None:
    scopes = build_structural_scopes(_nested_branch_graph())
    outer = scopes.branch("outer")
    inner = scopes.branch("inner")

    assert outer.nodeIds == ("outer_a", "outer_b", "inner_a", "inner_b")
    assert outer.armNodeIds["if"] == ("outer_a", "inner_a", "inner_b")
    assert outer.branchPointIds == ("pre",)
    assert "pre" not in outer.nodeIds
    assert inner.parentGroupId == "outer"
    assert outer.childGroupIds == ("inner",)


def test_straight_line_scopes_are_method_local_and_stop_at_branch_regions() -> None:
    scopes = build_structural_scopes(_nested_branch_graph())
    paths = {scope.nodeIds for scope in scopes.straightLines}

    assert paths == {("pre",), ("join", "after")}


def test_straight_line_scopes_do_not_cross_invoke_boundaries() -> None:
    graph = Graph.from_dict({
        "nodes": [
            {"id": "caller_entry", "type": "entry", "calleeFullName": "run"},
            {"id": "call", "type": "call", "calleeFullName": "helper", "callerMethod": "run"},
            {"id": "after", "type": "call", "calleeFullName": "Audit.record", "callerMethod": "run"},
            {"id": "callee_entry", "type": "entry", "calleeFullName": "helper"},
            {"id": "inside", "type": "call", "calleeFullName": "Worker.perform", "callerMethod": "helper"},
        ],
        "edges": [
            {"from": "caller_entry", "to": "call", "type": "sequence"},
            {"from": "call", "to": "after", "type": "sequence"},
            {"from": "call", "to": "callee_entry", "type": "invoke"},
            {"from": "callee_entry", "to": "inside", "type": "sequence"},
        ],
    })

    paths = {scope.nodeIds for scope in build_structural_scopes(graph).straightLines}

    assert paths == {("call", "after"), ("inside",)}
    assert classify_operation_roles(graph).operations["call"].role == "expanded-container"


def _guard_graph(*, compensating_work: bool = False) -> Graph:
    nodes = [
        {"id": "entry", "type": "entry", "calleeFullName": "run"},
        {"id": "check", "type": "call", "calleeFullName": "Validator.check", "callerMethod": "run"},
    ]
    edges = [{"from": "entry", "to": "check", "type": "sequence"}]
    if compensating_work:
        nodes.append({
            "id": "freeze", "type": "call", "calleeFullName": "Account.freeze", "callerMethod": "run",
            "branchArms": [{"groupId": "guard", "armLabel": "if"}],
        })
        edges.append({"from": "check", "to": "freeze", "type": "sequence"})
        exception_predecessor = "freeze"
    else:
        exception_predecessor = "check"
    nodes.extend([
        {
            "id": "exception", "type": "call",
            "calleeFullName": "java.lang.IllegalArgumentException.<init>:void(java.lang.String)",
            "callerMethod": "run", "deadEnd": True,
            "branchArms": [{"groupId": "guard", "armLabel": "if"}],
        },
        {"id": "commit", "type": "call", "calleeFullName": "Account.commit", "callerMethod": "run"},
    ])
    edges.extend([
        {"from": exception_predecessor, "to": "exception", "type": "sequence"},
        {"from": "check", "to": "commit", "type": "sequence"},
    ])
    return Graph.from_dict({
        "entryPoint": "run",
        "nodes": nodes,
        "edges": edges,
        "branchGroups": [{
            "id": "guard", "kind": "IF", "method": "run", "line": 2,
            "branchPointIds": ["check"],
            "arms": [
                {"label": "if", "empty": False, "terminus": "throw", "firstCallId": "freeze" if compensating_work else "exception"},
                {"label": "else", "empty": True, "terminus": "continues", "targetIds": ["commit"]},
            ],
        }],
    })


def test_exception_only_throw_arm_is_classified_as_guard() -> None:
    result = classify_operation_roles(_guard_graph())

    assert result.operations["exception"].role == "exception-mechanic"
    assert result.branches["guard"].exceptionOnlyArmLabels == ("if",)
    assert result.branches["guard"].purposefulArmLabels == ("else",)
    assert result.branches["guard"].isGuard is True
    assert result.graph.semanticFeatures["exception"].role == "exception-mechanic"


def test_compensating_work_keeps_throwing_arm_purposeful() -> None:
    result = classify_operation_roles(_guard_graph(compensating_work=True))

    assert result.operations["freeze"].role == "atomic"
    assert result.operations["exception"].role == "exception-mechanic"
    assert result.branches["guard"].purposefulArmLabels == ("if", "else")
    assert result.branches["guard"].exceptionOnlyArmLabels == ()
    assert result.branches["guard"].isGuard is False


def test_empty_internal_callee_is_atomic_not_expanded_container() -> None:
    graph = Graph.from_dict({
        "nodes": [
            {"id": "caller_entry", "type": "entry", "calleeFullName": "run"},
            {"id": "call", "type": "call", "calleeFullName": "empty", "callerMethod": "run"},
            {"id": "empty_entry", "type": "entry", "calleeFullName": "empty"},
        ],
        "edges": [
            {"from": "caller_entry", "to": "call", "type": "sequence"},
            {"from": "call", "to": "empty_entry", "type": "invoke"},
        ],
    })

    assert classify_operation_roles(graph).operations["call"].role == "atomic"


def test_exception_object_outside_throw_context_is_not_automatically_mechanical() -> None:
    graph = Graph.from_dict({
        "nodes": [{
            "id": "exception", "type": "call",
            "calleeFullName": "java.lang.IllegalStateException.<init>:void()",
            "callerMethod": "prepareError",
        }],
        "edges": [],
    })

    assert classify_operation_roles(graph).operations["exception"].role == "atomic"


def test_exception_only_callee_fallback_is_not_a_boundary_edge() -> None:
    graph = Graph.from_dict({
        "nodes": [
            {"id": "caller_entry", "type": "entry", "calleeFullName": "run"},
            {"id": "call", "type": "call", "calleeFullName": "fail", "callerMethod": "run"},
            {"id": "callee_entry", "type": "entry", "calleeFullName": "fail"},
            {
                "id": "exception", "type": "call",
                "calleeFullName": "java.lang.IllegalStateException.<init>:void()",
                "callerMethod": "fail", "deadEnd": True,
            },
            {"id": "after", "type": "call", "calleeFullName": "Service.after", "callerMethod": "run"},
        ],
        "edges": [
            {"from": "caller_entry", "to": "call", "type": "sequence"},
            {"from": "call", "to": "callee_entry", "type": "invoke"},
            {"from": "callee_entry", "to": "exception", "type": "sequence"},
            {"from": "callee_entry", "to": "after", "type": "sequence", "returnFrom": "call", "fallback": True},
        ],
    })

    result = classify_operation_roles(graph)

    assert result.operations["exception"].role == "exception-mechanic"
    assert result.ignoredFallbackEdges == frozenset({("callee_entry", "after")})
