"""Focused executable specification for Day 1 branch selection.

Run this file on its own while developing the branch-selection fix:

    poetry run pytest -q -s test/unit/test_branch_selection.py

The printed snapshot deliberately contains only the nodes, edges and branch
group involved in this guard clause:

    if (account == null) {
        throw new IllegalArgumentException("Unknown account");
    }
    ledger.noteAdjustment(account);
    account.getBalance();

The tests cover throw truncation, flattened arm targets, and the
backend-authoritative edge requirements used by frontend reachability.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


FLOWMAP_SRC = Path(__file__).resolve().parents[2] / "backend" / "src" / "flowmap"
sys.path.insert(0, str(FLOWMAP_SRC))

from domain.cfg_pipeline import filter_noise_cfg, flatten_cfg  # noqa: E402
from model import Graph  # noqa: E402


GROUP_ID = "cs_account_null"


def _node(
    node_id: str,
    node_type: str,
    callee: str,
    *,
    caller: str | None = None,
    terminus: str | None = None,
    arm: str | None = None,
) -> dict:
    node = {"id": node_id, "type": node_type, "calleeFullName": callee}
    if caller is not None:
        node["callerMethod"] = caller
    if terminus is not None:
        node["terminus"] = terminus
    if arm is not None:
        node["branchArms"] = [{"groupId": GROUP_ID, "armLabel": arm}]
    return node


def _edge(source: str, target: str, edge_type: str = "sequence") -> dict:
    return {"from": source, "to": target, "type": edge_type}


def _raw_guard_graph() -> Graph:
    """Raw Joern-like graph, including its false throw fall-through edge."""
    method = "AccountService.applyCompoundInterest"
    return Graph.from_dict(
        {
            "entryPoint": method,
            "nodes": [
                _node("entry", "entry", method),
                _node("condition", "call", "<operator>.equals", caller=method),
                _node(
                    "exception",
                    "call",
                    "IllegalArgumentException.<init>",
                    caller=method,
                    terminus="throw",
                    arm="if",
                ),
                _node("throw", "call", "<operator>.throw", caller=method, arm="if"),
                _node("note", "call", "TransferLedger.noteAdjustment", caller=method),
                _node("balance", "call", "Account.getBalance", caller=method),
                _node("exception_leaf", "leaf", "IllegalArgumentException.<init>", arm="if"),
                _node("note_leaf", "leaf", "TransferLedger.noteAdjustment"),
                _node("balance_leaf", "leaf", "Account.getBalance"),
            ],
            "edges": [
                _edge("entry", "condition"),
                _edge("condition", "exception"),
                _edge("condition", "note"),
                _edge("exception", "throw"),
                # Joern's incorrect lexical fall-through. Filtering must
                # remove it before bridging across the synthetic throw.
                _edge("throw", "note"),
                _edge("note", "balance"),
                _edge("exception", "exception_leaf", "invoke"),
                _edge("note", "note_leaf", "invoke"),
                _edge("balance", "balance_leaf", "invoke"),
            ],
            "branchGroups": [
                {
                    "id": GROUP_ID,
                    "kind": "IF",
                    "method": method,
                    "line": 10,
                    "arms": [
                        {
                            "label": "if",
                            "empty": False,
                            "terminus": "throw",
                            "conditionCode": "account == null",
                            "firstCallId": "exception",
                        },
                        {
                            "label": "else",
                            "empty": True,
                            "terminus": "continues",
                        },
                    ],
                }
            ],
        }
    )


def _flattened_guard() -> Graph:
    return flatten_cfg(filter_noise_cfg(_raw_guard_graph()))


def _branch_snapshot(graph: Graph) -> dict:
    """Small stable view intended for human inspection with pytest ``-s``."""
    interesting_orig_ids = {"entry", "exception", "note", "balance", "exception_leaf"}
    nodes = [
        node.to_dict()
        for node in graph.nodes
        if (node.origId or node.id) in interesting_orig_ids
    ]
    node_ids = {node["id"] for node in nodes}
    edges = [
        edge.to_dict()
        for edge in graph.edges
        if edge.source in node_ids and edge.target in node_ids
    ]
    return {
        "rootId": graph.rootId,
        "nodes": nodes,
        "edges": edges,
        "branchGroup": graph.branchGroups[0].to_dict(),
    }


class ThrowingGuardContractTests(unittest.TestCase):
    def test_throw_rejects_normal_flow_targets(self) -> None:
        with self.assertRaisesRegex(ValueError, "cannot have normal-flow targets"):
            Graph.from_dict({
                "branchGroups": [{
                    "id": "bad",
                    "kind": "IF",
                    "arms": [{
                        "label": "if",
                        "empty": False,
                        "terminus": "throw",
                        "targetIds": ["work"],
                    }],
                }],
            })

    def test_snapshot_and_throw_topology(self) -> None:
        graph = _flattened_guard()
        snapshot = _branch_snapshot(graph)
        print("\nFocused throwing-guard output:\n" + json.dumps(snapshot, indent=2))

        nodes_by_orig = {node.origId: node for node in graph.nodes}
        exception = nodes_by_orig["exception"]
        note = nodes_by_orig["note"]

        self.assertTrue(exception.deadEnd)
        self.assertEqual(exception.terminus, "throw")

        # The only outgoing edge from the dead end is constructor invocation.
        outgoing = [edge for edge in graph.edges if edge.source == exception.id]
        self.assertEqual([(edge.type, edge.target) for edge in outgoing], [
            ("invoke", nodes_by_orig["exception_leaf"].id),
        ])
        self.assertNotIn(
            (exception.id, note.id),
            {(edge.source, edge.target) for edge in graph.edges},
        )

    def test_current_branch_geometry_is_explicit(self) -> None:
        graph = _flattened_guard()
        group = graph.branchGroups[0]
        nodes_by_orig = {node.origId: node for node in graph.nodes}
        arms = {arm.label: arm for arm in group.arms}

        self.assertEqual(group.branchPointIds, [graph.rootId])
        self.assertEqual(group.convergesAt, nodes_by_orig["note"].id)
        self.assertEqual(arms["if"].firstCallId, nodes_by_orig["exception"].id)
        self.assertEqual(arms["if"].terminus, "throw")
        self.assertTrue(arms["else"].empty)
        self.assertEqual(arms["else"].terminus, "continues")

    def test_arm_target_ids(self) -> None:
        """Arm exits are explicit and never contradict throw."""
        graph = _flattened_guard()
        group_json = graph.branchGroups[0].to_dict()
        arms = {arm["label"]: arm for arm in group_json["arms"]}
        nodes_by_orig = {node.origId: node for node in graph.nodes}

        self.assertEqual(arms["if"]["targetIds"], [])
        self.assertEqual(arms["else"]["targetIds"], [nodes_by_orig["note"].id])

    def test_route_edges_carry_arm_requirements(self) -> None:
        graph = _flattened_guard()
        group = graph.branchGroups[0]
        nodes_by_orig = {node.origId: node for node in graph.nodes}

        into_throw = next(
            edge for edge in graph.edges
            if edge.target == nodes_by_orig["exception"].id
        )
        into_normal = next(
            edge for edge in graph.edges
            if edge.target == nodes_by_orig["note"].id
        )

        self.assertEqual(
            [(r.groupId, r.armLabel) for r in into_throw.branchRequirements],
            [(group.id, "if")],
        )
        self.assertEqual(
            [(r.groupId, r.armLabel) for r in into_normal.branchRequirements],
            [(group.id, "else")],
        )


if __name__ == "__main__":
    unittest.main()
