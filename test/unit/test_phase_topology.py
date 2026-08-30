import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend" / "src" / "flowmap"))

from domain.phase_topology import graph_call_sequence_pairs
from model import Edge, Graph, Node


METHOD = "Example.run:void()"


def test_structure_anchors_are_transparent_between_calls() -> None:
    graph = Graph(
        nodes=[
            Node("before", "call", callerMethod=METHOD),
            Node("branch_entry", "structure", callerMethod=METHOD, structureGroupId="g", structureRole="entry"),
            Node("decision", "structure", callerMethod=METHOD, structureGroupId="g", structureRole="decision"),
            Node("body", "call", callerMethod=METHOD),
            Node("branch_exit", "structure", callerMethod=METHOD, structureGroupId="g", structureRole="exit"),
            Node("after", "call", callerMethod=METHOD),
        ],
        edges=[
            Edge("before", "branch_entry", "sequence"),
            Edge("branch_entry", "decision", "sequence"),
            Edge("decision", "body", "sequence"),
            Edge("body", "branch_exit", "sequence"),
            Edge("branch_exit", "after", "sequence"),
        ],
    )

    assert graph_call_sequence_pairs(graph) == [
        ("before", "body"), ("body", "after")
    ]


def test_transfer_is_topological_but_method_exit_is_a_boundary() -> None:
    graph = Graph(
        nodes=[
            Node("body", "call", callerMethod=METHOD),
            Node("continue", "transfer", callerMethod=METHOD, transferKind="continue", targetStructureGroupId="loop"),
            Node("loop_exit", "structure", callerMethod=METHOD, structureGroupId="loop", structureRole="exit"),
            Node("after", "call", callerMethod=METHOD),
            Node("return", "exit", callerMethod=METHOD, exitKind="return"),
            Node("unreachable", "call", callerMethod=METHOD),
        ],
        edges=[
            Edge("body", "continue", "sequence"),
            Edge("continue", "loop_exit", "sequence"),
            Edge("loop_exit", "after", "sequence"),
            Edge("after", "return", "sequence"),
            Edge("return", "unreachable", "sequence"),
        ],
    )

    assert graph_call_sequence_pairs(graph) == [("body", "after")]


def test_projection_never_crosses_method_ownership() -> None:
    graph = Graph(
        nodes=[
            Node("caller", "call", callerMethod="Caller.run:void()"),
            Node("anchor", "structure", callerMethod="Caller.run:void()"),
            Node("callee", "call", callerMethod="Callee.run:void()"),
        ],
        edges=[
            Edge("caller", "anchor", "sequence"),
            Edge("anchor", "callee", "sequence"),
        ],
    )

    assert graph_call_sequence_pairs(graph) == []
