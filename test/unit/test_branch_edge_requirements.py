from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend" / "src" / "flowmap"))

from domain.cfg_pipeline import flatten_cfg  # noqa: E402
from model import Graph  # noqa: E402


def node(id_, type_, callee=None, caller=None, arms=None):
    value = {"id": id_, "type": type_}
    if callee is not None:
        value["calleeFullName"] = callee
    if caller is not None:
        value["callerMethod"] = caller
    if arms:
        value["branchArms"] = [
            {"groupId": group, "armLabel": arm} for group, arm in arms
        ]
    return value


def edge(source, target, type_="sequence"):
    return {"from": source, "to": target, "type": type_}


def requirement_set(edge_):
    return {(item.groupId, item.armLabel) for item in edge_.branchRequirements}


def route(graph, source_orig, target_orig):
    nodes = {item.id: item.origId for item in graph.nodes}
    return next(
        item for item in graph.edges
        if nodes.get(item.source) == source_orig and nodes.get(item.target) == target_orig
        and item.type == "sequence"
    )


def branch_graph():
    method = "run"
    return {
        "entryPoint": method,
        "nodes": [
            node("e", "entry", method), node("c_condition", "call", "Condition.check", method),
            node("c_if_1", "call", "If.one", method, [("outer", "if")]),
            node("c_if_2", "call", "If.two", method, [("outer", "if")]),
            node("c_else", "call", "Else.run", method, [("outer", "else")]),
            node("c_join", "call", "Join.run", method),
            node("leaf_condition", "leaf", "Condition.check"), node("leaf_if_1", "leaf", "If.one"),
            node("leaf_if_2", "leaf", "If.two"), node("leaf_else", "leaf", "Else.run"),
            node("leaf_join", "leaf", "Join.run"),
        ],
        "edges": [
            edge("e", "c_condition"), edge("c_condition", "c_if_1"), edge("c_condition", "c_else"),
            edge("c_if_1", "c_if_2"), edge("c_if_2", "c_join"), edge("c_else", "c_join"),
            edge("c_condition", "leaf_condition", "invoke"), edge("c_if_1", "leaf_if_1", "invoke"),
            edge("c_if_2", "leaf_if_2", "invoke"), edge("c_else", "leaf_else", "invoke"),
            edge("c_join", "leaf_join", "invoke"),
        ],
        "branchGroups": [{"id": "outer", "kind": "IF", "method": method,
            "branchPointIds": ["c_condition"], "arms": [
                {"label": "if", "firstCallId": "c_if_1", "terminus": "continues"},
                {"label": "else", "firstCallId": "c_else", "terminus": "continues"},
            ]}],
    }


class BranchEdgeRequirementTests(unittest.TestCase):
    def test_branch_point_to_each_arm_head_has_selected_arm_requirement(self):
        flattened = flatten_cfg(Graph.from_dict(branch_graph()))
        group = flattened.branchGroups[0]
        self.assertEqual(requirement_set(route(flattened, "leaf_condition", "c_if_1")), {(group.id, "if")})
        self.assertEqual(requirement_set(route(flattened, "leaf_condition", "c_else")), {(group.id, "else")})

    def test_edge_within_branch_arm_keeps_requirement(self):
        flattened = flatten_cfg(Graph.from_dict(branch_graph()))
        group = flattened.branchGroups[0]
        self.assertEqual(requirement_set(route(flattened, "leaf_if_1", "c_if_2")), {(group.id, "if")})

    def test_edge_within_nested_branch_has_outer_and_inner_requirements(self):
        raw = branch_graph()
        raw["nodes"].extend([
            node("c_inner_if", "call", "Inner.yes", "run", [("outer", "if"), ("inner", "if")]),
            node("c_inner_else", "call", "Inner.no", "run", [("outer", "if"), ("inner", "else")]),
            node("leaf_inner_if", "leaf", "Inner.yes"), node("leaf_inner_else", "leaf", "Inner.no"),
        ])
        raw["edges"] = [item for item in raw["edges"] if item != edge("c_if_1", "c_if_2")]
        raw["edges"].extend([
            edge("c_if_1", "c_inner_if"), edge("c_if_1", "c_inner_else"),
            edge("c_inner_if", "c_if_2"), edge("c_inner_else", "c_if_2"),
            edge("c_inner_if", "leaf_inner_if", "invoke"), edge("c_inner_else", "leaf_inner_else", "invoke"),
        ])
        raw["branchGroups"].append({"id": "inner", "kind": "IF", "method": "run",
            "branchPointIds": ["c_if_1"], "arms": [
                {"label": "if", "firstCallId": "c_inner_if", "terminus": "continues"},
                {"label": "else", "firstCallId": "c_inner_else", "terminus": "continues"},
            ]})
        flattened = flatten_cfg(Graph.from_dict(raw))
        groups = {item.id.split("~", 1)[0]: item for item in flattened.branchGroups}
        self.assertEqual(
            requirement_set(route(flattened, "leaf_if_1", "c_inner_if")),
            {(groups["outer"].id, "if"), (groups["inner"].id, "if")},
        )

    def test_external_leaf_to_later_branch_condition_has_no_requirement(self):
        raw = branch_graph()
        raw["nodes"].insert(1, node("c_load", "call", "Session.get", "run"))
        raw["nodes"].append(node("leaf_load", "leaf", "Session.get"))
        raw["edges"] = [item for item in raw["edges"] if item != edge("e", "c_condition")]
        raw["edges"].extend([
            edge("e", "c_load"), edge("c_load", "c_condition"), edge("c_load", "leaf_load", "invoke")
        ])
        flattened = flatten_cfg(Graph.from_dict(raw))
        boundary = route(flattened, "leaf_load", "c_condition")
        self.assertEqual(requirement_set(boundary), set())
        self.assertIsNotNone(boundary.returnFrom)

    def test_every_branch_to_linear_boundary_keeps_its_originating_arm(self):
        flattened = flatten_cfg(Graph.from_dict(branch_graph()))
        group = flattened.branchGroups[0]
        self.assertEqual(requirement_set(route(flattened, "leaf_if_2", "c_join")), {(group.id, "if")})
        self.assertEqual(requirement_set(route(flattened, "leaf_else", "c_join")), {(group.id, "else")})

    def test_boundary_between_consecutive_groups_keeps_previous_and_next_selection(self):
        raw = branch_graph()
        raw["nodes"].extend([
            node("c_second_if", "call", "Second.yes", "run", [("second", "if")]),
            node("c_second_else", "call", "Second.no", "run", [("second", "else")]),
            node("leaf_second_if", "leaf", "Second.yes"), node("leaf_second_else", "leaf", "Second.no"),
        ])
        raw["edges"] = [item for item in raw["edges"] if item["to"] != "c_join"]
        raw["edges"].extend([
            edge("c_if_2", "c_second_if"), edge("c_if_2", "c_second_else"),
            edge("c_else", "c_second_if"), edge("c_else", "c_second_else"),
            edge("c_second_if", "c_join"), edge("c_second_else", "c_join"),
            edge("c_second_if", "leaf_second_if", "invoke"), edge("c_second_else", "leaf_second_else", "invoke"),
        ])
        raw["branchGroups"].append({"id": "second", "kind": "IF", "method": "run",
            "branchPointIds": ["c_if_2", "c_else"], "arms": [
                {"label": "if", "firstCallId": "c_second_if", "terminus": "continues"},
                {"label": "else", "firstCallId": "c_second_else", "terminus": "continues"},
            ]})
        flattened = flatten_cfg(Graph.from_dict(raw))
        groups = {item.id.split("~", 1)[0]: item for item in flattened.branchGroups}
        for source, outer_label in (("leaf_if_2", "if"), ("leaf_else", "else")):
            for target, second_label in (("c_second_if", "if"), ("c_second_else", "else")):
                self.assertEqual(
                    requirement_set(route(flattened, source, target)),
                    {(groups["outer"].id, outer_label), (groups["second"].id, second_label)},
                )


if __name__ == "__main__":
    unittest.main()
