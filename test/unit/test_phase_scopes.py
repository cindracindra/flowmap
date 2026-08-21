from __future__ import annotations

import sys
from pathlib import Path

FLOWMAP_SRC = Path(__file__).resolve().parents[2] / "backend" / "src" / "flowmap"
sys.path.insert(0, str(FLOWMAP_SRC))

from domain.phase_scopes import build_scopes  # noqa: E402
from model import Graph  # noqa: E402


def graph(nodes, edges, groups=()) -> Graph:
    return Graph.from_dict({
        "entryPoint": "run",
        "nodes": [{"id": "entry", "type": "entry", "calleeFullName": "run"}, *nodes],
        "edges": edges,
        "branchGroups": list(groups),
    })


def call(node_id: str, *arms: tuple[str, str]) -> dict:
    return {
        "id": node_id, "type": "call", "calleeFullName": f"S.{node_id}",
        "callerMethod": "run",
        "branchArms": [{"groupId": g, "armLabel": a} for g, a in arms],
    }


def seq(*pairs) -> list[dict]:
    return [{"from": a, "to": b, "type": "sequence"} for a, b in pairs]


def runs(result, entry="entry") -> list[tuple[str, ...]]:
    return [scope.nodeIds for scope in result[entry]]


def test_a_straight_line_is_one_run() -> None:
    subject = graph([call("a"), call("b"), call("c")],
                    seq(("entry", "a"), ("a", "b"), ("b", "c")))

    assert runs(build_scopes(subject)) == [("a", "b", "c")]


def test_a_branch_splits_into_pre_arms_and_post() -> None:
    # a; if (…) { x } else { y }; z
    subject = graph(
        [call("a"), call("x", ("g", "if")), call("y", ("g", "else")), call("z")],
        seq(("entry", "a"), ("a", "x"), ("a", "y"), ("x", "z"), ("y", "z")),
        [{"id": "g", "kind": "IF", "method": "run",
          "arms": [{"label": "if", "empty": False, "terminus": "continues"},
                   {"label": "else", "empty": False, "terminus": "continues"}]}],
    )

    assert runs(build_scopes(subject)) == [("a",), ("x",), ("y",), ("z",)]


def test_arm_contents_are_segmented_like_any_other_sequence() -> None:
    # The defect this fixes: arm members were previously excluded from every
    # straight-line scope, so an arm of any size was never divided.
    subject = graph(
        [call("a"),
         call("x1", ("g", "if")), call("x2", ("g", "if")), call("x3", ("g", "if"))],
        seq(("entry", "a"), ("a", "x1"), ("x1", "x2"), ("x2", "x3")),
        [{"id": "g", "kind": "IF", "method": "run",
          "arms": [{"label": "if", "empty": False, "terminus": "continues"}]}],
    )

    assert runs(build_scopes(subject)) == [("a",), ("x1", "x2", "x3")]


def test_a_nested_branch_becomes_its_own_region() -> None:
    subject = graph(
        [call("a"),
         call("x", ("outer", "if")),
         call("n", ("outer", "if"), ("inner", "if")),
         call("y", ("outer", "if"))],
        seq(("entry", "a"), ("a", "x"), ("x", "n"), ("n", "y")),
        [{"id": "outer", "kind": "IF", "method": "run",
          "arms": [{"label": "if", "empty": False, "terminus": "continues"}]},
         {"id": "inner", "kind": "IF", "method": "run",
          "arms": [{"label": "if", "empty": False, "terminus": "continues"}]}],
    )

    result = build_scopes(subject)

    assert runs(result) == [("a",), ("x",), ("n",), ("y",)]
    by_nodes = {scope.nodeIds: scope.tags for scope in result["entry"]}
    assert by_nodes[("a",)] == frozenset()
    assert by_nodes[("x",)] == frozenset({("outer", "if")})
    assert by_nodes[("n",)] == frozenset({("outer", "if"), ("inner", "if")})
    # Nesting is set inclusion: the inner region's tags are a superset.
    assert by_nodes[("x",)] < by_nodes[("n",)]


def test_a_loop_yields_setup_body_and_tail() -> None:
    # for (item : items()) { read(item); write(item); }  then  done()
    # The iterable forks into the body and past it; the body's tail both loops
    # back and exits, so `done` has two predecessors.
    subject = graph(
        [call("items"), call("read"), call("write"), call("done")],
        seq(("entry", "items"),
            ("items", "read"), ("items", "done"),
            ("read", "write"),
            ("write", "read"), ("write", "done")),
    )

    assert runs(build_scopes(subject)) == [("items",), ("read", "write"), ("done",)]


def test_a_pure_cycle_still_produces_a_run() -> None:
    subject = graph([call("a"), call("b")], seq(("a", "b"), ("b", "a")))

    assert runs(build_scopes(subject)) == [("a", "b")]


def test_excluded_operations_are_dropped() -> None:
    subject = graph([call("a"), call("boom")], seq(("entry", "a"), ("a", "boom")))

    assert runs(build_scopes(subject, {"boom": "exception-mechanic"})) == [("a",)]


def test_a_wholly_excluded_arm_contributes_no_scope() -> None:
    subject = graph(
        [call("a"), call("msg", ("g", "if")), call("boom", ("g", "if"))],
        seq(("entry", "a"), ("a", "msg"), ("msg", "boom")),
        [{"id": "g", "kind": "IF", "method": "run",
          "arms": [{"label": "if", "empty": False, "terminus": "throw"}]}],
    )
    excluded = {"msg": "in-throwing-arm", "boom": "exception-mechanic"}

    assert runs(build_scopes(subject, excluded)) == [("a",)]


def test_scopes_never_span_two_methods() -> None:
    subject = Graph.from_dict({
        "nodes": [
            {"id": "e1", "type": "entry", "calleeFullName": "caller"},
            {"id": "a", "type": "call", "calleeFullName": "S.a", "callerMethod": "caller"},
            {"id": "e2", "type": "entry", "calleeFullName": "callee"},
            {"id": "b", "type": "call", "calleeFullName": "S.b", "callerMethod": "callee"},
        ],
        "edges": [
            {"from": "e1", "to": "a", "type": "sequence"},
            {"from": "a", "to": "e2", "type": "invoke"},
            {"from": "e2", "to": "b", "type": "sequence"},
        ],
    })

    result = build_scopes(subject)

    assert runs(result, "e1") == [("a",)]
    assert runs(result, "e2") == [("b",)]


def test_return_edges_do_not_join_a_run() -> None:
    # Guards the module against being handed a flattened graph: a synthesized
    # return edge is not intra-method adjacency.
    subject = graph(
        [call("a"), call("b")],
        [{"from": "entry", "to": "a", "type": "sequence"},
         {"from": "a", "to": "b", "type": "sequence", "returnFrom": "a"}],
    )

    assert runs(build_scopes(subject)) == [("a",), ("b",)]


def test_output_is_stable_under_edge_order() -> None:
    nodes = [call("a"), call("b"), call("c")]
    forward = seq(("entry", "a"), ("a", "b"), ("b", "c"))

    assert runs(build_scopes(graph(nodes, forward))) == runs(
        build_scopes(graph(nodes, list(reversed(forward))))
    )
