import sys
from pathlib import Path

FLOWMAP_SRC = Path(__file__).resolve().parents[2] / "backend" / "src" / "flowmap"
sys.path.insert(0, str(FLOWMAP_SRC))

from domain.cfg_slicing import classify_roots_and_orphans  # noqa: E402
from model import Graph  # noqa: E402


def test_structural_branch_without_calls_is_an_orphan() -> None:
    graph = Graph.from_dict({
        "nodes": [
            {"id": "entry", "type": "entry", "calleeFullName": "Example.empty"},
            {
                "id": "decision",
                "type": "structure",
                "callerMethod": "Example.empty",
                "structureGroupId": "branch",
                "structureRole": "decision",
                "code": "if (condition)",
            },
            {"id": "exit", "type": "exit", "callerMethod": "Example.empty"},
        ],
        "edges": [
            {"from": "entry", "to": "decision", "type": "sequence"},
            {"from": "decision", "to": "exit", "type": "sequence"},
        ],
    })

    classified = classify_roots_and_orphans(graph)

    assert classified.roots == []
    assert classified.orphans == ["entry"]


def test_anchor_and_transfer_only_method_is_an_orphan() -> None:
    graph = Graph.from_dict({
        "nodes": [
            {"id": "entry", "type": "entry", "calleeFullName": "Example.empty"},
            {"id": "loop_entry", "type": "structure", "callerMethod": "Example.empty", "structureGroupId": "loop", "structureRole": "entry"},
            {"id": "break", "type": "transfer", "callerMethod": "Example.empty", "transferKind": "break", "targetStructureGroupId": "loop"},
            {"id": "loop_exit", "type": "structure", "callerMethod": "Example.empty", "structureGroupId": "loop", "structureRole": "exit"},
        ],
        "edges": [
            {"from": "entry", "to": "loop_entry", "type": "sequence"},
            {"from": "loop_entry", "to": "break", "type": "sequence"},
            {"from": "break", "to": "loop_exit", "type": "sequence"},
        ],
    })

    classified = classify_roots_and_orphans(graph)

    assert classified.roots == []
    assert classified.orphans == ["entry"]


def test_method_with_surviving_call_remains_a_root() -> None:
    graph = Graph.from_dict({
        "nodes": [
            {"id": "entry", "type": "entry", "calleeFullName": "Example.run"},
            {
                "id": "call",
                "type": "call",
                "callerMethod": "Example.run",
                "calleeFullName": "Work.execute",
            },
        ],
        "edges": [{"from": "entry", "to": "call", "type": "sequence"}],
    })

    classified = classify_roots_and_orphans(graph)

    assert classified.roots == ["entry"]
    assert classified.orphans == []
