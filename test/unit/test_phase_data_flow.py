from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend" / "src" / "flowmap"))

from domain.phase_data_flow import build_phase_data_flow_questions  # noqa: E402
from model import Edge, Graph, Node  # noqa: E402


def _graph(edges: list[Edge]) -> Graph:
    return Graph(
        nodes=[
            Node("entry", "entry"),
            Node("a", "call"),
            Node("b", "call"),
            Node("c", "call"),
            Node("exit", "exit"),
            Node("leaf", "leaf"),
        ],
        edges=edges,
    )


def test_groups_unique_sequence_sources_by_target_deterministically():
    graph = _graph([
        Edge("b", "c", "sequence"),
        Edge("a", "c", "sequence"),
        Edge("a", "c", "sequence"),
        Edge("a", "b", "sequence"),
    ])

    assert build_phase_data_flow_questions(graph) == {
        "b": ["a"],
        "c": ["a", "b"],
    }


def test_excludes_edges_that_execution_phase_cohesion_does_not_compare():
    graph = _graph([
        Edge("entry", "a", "sequence"),
        Edge("a", "exit", "sequence"),
        Edge("a", "leaf", "sequence"),
        Edge("a", "b", "invoke"),
        Edge("a", "b", "data"),
        Edge("missing", "b", "sequence"),
    ])

    assert build_phase_data_flow_questions(graph) == {}


def test_keeps_forward_branch_transitions_as_separate_sources_for_one_target():
    graph = _graph([
        Edge("a", "b", "sequence"),
        Edge("a", "c", "sequence"),
        Edge("b", "c", "sequence"),
    ])

    assert build_phase_data_flow_questions(graph) == {
        "b": ["a"],
        "c": ["a", "b"],
    }


def test_structural_anchors_do_not_hide_a_ddg_candidate_pair():
    graph = _graph([
        Edge("a", "branch_entry", "sequence"),
        Edge("branch_entry", "decision", "sequence"),
        Edge("decision", "b", "sequence"),
    ])
    graph.nodes.extend([
        Node("branch_entry", "structure"),
        Node("decision", "structure"),
    ])

    assert build_phase_data_flow_questions(graph) == {"b": ["a"]}
