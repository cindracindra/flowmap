from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

FLOWMAP_SRC = Path(__file__).resolve().parents[2] / "backend" / "src" / "flowmap"
sys.path.insert(0, str(FLOWMAP_SRC))

from domain.phase_labelling import label_phases  # noqa: E402
from model import Graph, Phase  # noqa: E402


def test_labels_final_flattened_phases_without_changing_membership() -> None:
    graph = Graph.from_dict({
        "entryPoint": "main",
        "nodes": [
            {"id": "validate~1", "origId": "validate", "type": "call"},
            {"id": "reserve~2", "origId": "reserve", "type": "call"},
            {"id": "notify~3", "origId": "notify", "type": "call"},
        ],
        "edges": [],
    })
    phases = [
        Phase(id="phase-1", nodes=["validate~1", "reserve~2"]),
        Phase(id="phase-2", nodes=["notify~3"]),
    ]
    original_nodes = [list(phase.nodes) for phase in phases]
    labeler = MagicMock(side_effect=["  Stock Reservation  ", "Send Notice"])

    assert label_phases(graph, phases, labeler) == 2

    assert [phase.label for phase in phases] == [
        "Stock Reservation", "Send Notice"
    ]
    assert [phase.nodes for phase in phases] == original_nodes
    assert [phase.id for phase in phases] == ["phase-1", "phase-2"]
    assert labeler.call_args_list[0].args == (
        graph, ("validate~1", "reserve~2"), 0
    )
    assert labeler.call_args_list[1].args == (graph, ("notify~3",), 1)


def test_failed_or_blank_label_response_leaves_phase_unnamed() -> None:
    graph = Graph.from_dict({
        "nodes": [
            {"id": "one~1", "origId": "one", "type": "call"},
            {"id": "two~2", "origId": "two", "type": "call"},
        ],
        "edges": [],
    })
    phases = [Phase(nodes=["one~1"]), Phase(nodes=["two~2"])]
    labeler = MagicMock(side_effect=[None, "   "])

    assert label_phases(graph, phases, labeler) == 0
    assert [phase.label for phase in phases] == [None, None]
