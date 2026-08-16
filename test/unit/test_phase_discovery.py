from __future__ import annotations

import sys
from pathlib import Path

FLOWMAP_SRC = Path(__file__).resolve().parents[2] / "backend" / "src" / "flowmap"
sys.path.insert(0, str(FLOWMAP_SRC))

from domain.phase_discovery import build_phase_tree  # noqa: E402
from model import Edge, Graph, Node  # noqa: E402


def _polymorphic_graph(reverse_targets: bool = False) -> Graph:
    nodes = [
        Node("root", "entry", calleeFullName="run"),
        Node("area", "call", calleeFullName="Shape.area"),
        Node("circle_entry", "entry", calleeFullName="Circle.area"),
        Node("circle", "call", calleeFullName="Circle.calc"),
        Node("square_entry", "entry", calleeFullName="Square.area"),
        Node("square", "call", calleeFullName="Square.calc"),
        Node("after", "call", calleeFullName="Logger.log"),
    ]
    invoke_edges = [
        Edge("area", "circle_entry", "invoke"),
        Edge("area", "square_entry", "invoke"),
    ]
    if reverse_targets:
        invoke_edges.reverse()
    return Graph(
        entryPoint="run",
        rootId="root",
        nodes=nodes,
        edges=[
            Edge("root", "area", "sequence"),
            *invoke_edges,
            Edge("circle_entry", "circle", "sequence"),
            Edge("square_entry", "square", "sequence"),
            Edge("circle", "after", "sequence", returnFrom="area"),
            Edge("square", "after", "sequence", returnFrom="area"),
        ],
    )


def test_polymorphic_call_site_is_not_a_member_of_multiple_phases() -> None:
    phases = build_phase_tree(_polymorphic_graph())["phases"]
    members = [node_id for phase in phases for node_id in phase["nodes"]]

    assert members.count("area") == 0
    assert len(members) == len(set(members))
    assert {"circle", "square", "after"} == set(members)


def test_all_dispatch_targets_precede_continuation_regardless_of_target_order() -> None:
    for reverse_targets in (False, True):
        phases = build_phase_tree(_polymorphic_graph(reverse_targets))["phases"]
        phase_index = {
            node_id: index
            for index, phase in enumerate(phases)
            for node_id in phase["nodes"]
        }

        assert phase_index["circle"] < phase_index["after"]
        assert phase_index["square"] < phase_index["after"]
        assert phases[phase_index["after"]]["opened_by"]["reason"] == "gate"
