from __future__ import annotations

import sys
from pathlib import Path

FLOWMAP_SRC = Path(__file__).resolve().parents[2] / "backend" / "src" / "flowmap"
sys.path.insert(0, str(FLOWMAP_SRC))

from domain.phase_exclusion import find_excluded_operations  # noqa: E402
from model import Graph  # noqa: E402


def _guard_graph(*, compensating_work: bool = False) -> Graph:
    """`if (bad) { [freeze();] throw new IllegalArgumentException(...); } commit();`"""
    nodes = [
        {"id": "entry", "type": "entry", "calleeFullName": "run"},
        {"id": "check", "type": "call", "calleeFullName": "Validator.check", "callerMethod": "run"},
    ]
    edges = [{"from": "entry", "to": "check", "type": "sequence"}]
    if compensating_work:
        nodes.append({
            "id": "freeze", "type": "call", "calleeFullName": "Account.freeze",
            "callerMethod": "run",
            "branchArms": [{"groupId": "guard", "armLabel": "if"}],
        })
        edges.append({"from": "check", "to": "freeze", "type": "sequence"})
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
        {"from": "freeze" if compensating_work else "check", "to": "exception", "type": "sequence"},
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
                {"label": "if", "empty": False, "terminus": "throw"},
                {"label": "else", "empty": True, "terminus": "continues"},
            ],
        }],
    })


def test_exception_constructor_in_a_throwing_arm_is_excluded_as_a_mechanic() -> None:
    # Both rules select it; the more specific reason wins.
    assert find_excluded_operations(_guard_graph())["exception"] == "exception-mechanic"


def test_operations_outside_the_throwing_arm_are_untouched() -> None:
    excluded = find_excluded_operations(_guard_graph())

    assert "commit" not in excluded
    assert "check" not in excluded          # the condition stays an ordinary operation


def test_compensating_work_in_a_throwing_arm_is_excluded_by_the_blanket_rule() -> None:
    # Deliberate, knowingly accepted trade: "refund, then throw" loses the refund
    # from the phase view. The old name-heuristic rule kept `freeze` purposeful;
    # the arm rule does not, in exchange for needing no data-flow test.
    excluded = find_excluded_operations(_guard_graph(compensating_work=True))

    assert excluded["freeze"] == "in-throwing-arm"
    assert excluded["exception"] == "exception-mechanic"


def test_unconditional_throw_has_no_arm_and_still_excludes_its_constructor() -> None:
    graph = Graph.from_dict({
        "nodes": [
            {"id": "entry", "type": "entry", "calleeFullName": "fail"},
            {
                "id": "exception", "type": "call",
                "calleeFullName": "java.lang.IllegalStateException.<init>:void()",
                "callerMethod": "fail", "deadEnd": True,
            },
        ],
        "edges": [{"from": "entry", "to": "exception", "type": "sequence"}],
    })

    assert find_excluded_operations(graph) == {"exception": "exception-mechanic"}


def test_exception_object_that_is_not_a_dead_end_is_kept() -> None:
    # Constructed, stored, thrown later or not at all -- ordinary work.
    graph = Graph.from_dict({
        "nodes": [{
            "id": "exception", "type": "call",
            "calleeFullName": "java.lang.IllegalStateException.<init>:void()",
            "callerMethod": "prepareError",
        }],
        "edges": [],
    })

    assert find_excluded_operations(graph) == {}


def test_real_work_immediately_before_a_throw_is_kept() -> None:
    # `audit(o); throw e;` -- audit's only next call is the throw operator, so it
    # is a dead end, but it is not constructing the exception and sits in no arm.
    graph = Graph.from_dict({
        "nodes": [
            {"id": "entry", "type": "entry", "calleeFullName": "fail"},
            {
                "id": "audit", "type": "call", "calleeFullName": "Audit.record:void(Order)",
                "callerMethod": "fail", "deadEnd": True,
            },
        ],
        "edges": [{"from": "entry", "to": "audit", "type": "sequence"}],
    })

    assert find_excluded_operations(graph) == {}


def test_returning_and_continuing_arms_exclude_nothing() -> None:
    graph = Graph.from_dict({
        "nodes": [
            {"id": "entry", "type": "entry", "calleeFullName": "run"},
            {
                "id": "early", "type": "call", "calleeFullName": "Service.early",
                "callerMethod": "run",
                "branchArms": [{"groupId": "g", "armLabel": "if"}],
            },
        ],
        "edges": [{"from": "entry", "to": "early", "type": "sequence"}],
        "branchGroups": [{
            "id": "g", "kind": "IF", "method": "run",
            "arms": [
                {"label": "if", "empty": False, "terminus": "return"},
                {"label": "else", "empty": True, "terminus": "continues"},
            ],
        }],
    })

    assert find_excluded_operations(graph) == {}


def test_a_node_in_a_nested_arm_is_excluded_when_any_of_its_arms_throws() -> None:
    graph = Graph.from_dict({
        "nodes": [
            {"id": "entry", "type": "entry", "calleeFullName": "run"},
            {
                "id": "nested", "type": "call", "calleeFullName": "Service.work",
                "callerMethod": "run",
                "branchArms": [
                    {"groupId": "outer", "armLabel": "if"},
                    {"groupId": "inner", "armLabel": "if"},
                ],
            },
        ],
        "edges": [{"from": "entry", "to": "nested", "type": "sequence"}],
        "branchGroups": [
            {"id": "outer", "kind": "IF", "method": "run",
             "arms": [{"label": "if", "empty": False, "terminus": "continues"}]},
            {"id": "inner", "kind": "IF", "method": "run",
             "arms": [{"label": "if", "empty": False, "terminus": "throw"}]},
        ],
    })

    assert find_excluded_operations(graph) == {"nested": "in-throwing-arm"}


def test_entries_and_leaves_are_never_excluded() -> None:
    graph = Graph.from_dict({
        "nodes": [
            {"id": "entry", "type": "entry", "calleeFullName": "run", "deadEnd": True},
            {
                "id": "leaf", "type": "leaf",
                "calleeFullName": "java.lang.IllegalStateException.<init>:void()",
                "deadEnd": True,
                "branchArms": [{"groupId": "g", "armLabel": "if"}],
            },
        ],
        "edges": [],
        "branchGroups": [{
            "id": "g", "kind": "IF", "method": "run",
            "arms": [{"label": "if", "empty": False, "terminus": "throw"}],
        }],
    })

    assert find_excluded_operations(graph) == {}


def test_receiver_type_identifies_the_exception_when_the_callee_is_unresolved() -> None:
    graph = Graph.from_dict({
        "nodes": [{
            "id": "exception", "type": "call", "callerMethod": "fail", "deadEnd": True,
        }],
        "edges": [],
        "semanticFeatures": {
            "exception": {"receiverType": "org.example.InsufficientFundsException"},
        },
    })

    assert find_excluded_operations(graph) == {"exception": "exception-mechanic"}
