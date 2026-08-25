from __future__ import annotations

import sys
from pathlib import Path

FLOWMAP_SRC = Path(__file__).resolve().parents[2] / "backend" / "src" / "flowmap"
sys.path.insert(0, str(FLOWMAP_SRC))

from domain.cfg_flattening import flatten_cfg  # noqa: E402
from domain.phase_overlay import (  # noqa: E402
    overlay_phase_tree,
    overlay_phases,
    phase_tree_dict,
)
from domain.phase_resolution import resolve_uncertain_gates  # noqa: E402
from domain.phase_retention import recheck_lapsed_retentions  # noqa: E402
from domain.phase_segmentation import analyse  # noqa: E402
from model import Graph  # noqa: E402


def _features(identity: str) -> dict:
    return {
        "receiver": identity,
        "inputIdentifiers": [identity],
        "arguments": [identity],
        "observedFeatures": ["receiver", "inputs", "arguments"],
    }


def _uncertain(term: str) -> dict:
    return {"methodTerms": [term], "observedFeatures": ["methodTerms"]}


def _originals(flattened: Graph, node_ids: list[str]) -> list[str]:
    nodes = {node.id: node for node in flattened.nodes}
    return [nodes[node_id].origId or nodes[node_id].id for node_id in node_ids]


def test_replays_two_instances_of_one_method_with_distinct_clone_ids() -> None:
    graph = Graph.from_dict({
        "entryPoint": "main",
        "roots": ["main_entry"],
        "nodes": [
            {"id": "main_entry", "type": "entry", "calleeFullName": "main"},
            {"id": "call_one", "type": "call", "callerMethod": "main"},
            {"id": "call_two", "type": "call", "callerMethod": "main"},
            {"id": "helper_entry", "type": "entry", "calleeFullName": "helper"},
            {"id": "work", "type": "call", "callerMethod": "helper"},
        ],
        "edges": [
            {"from": "main_entry", "to": "call_one", "type": "sequence"},
            {"from": "call_one", "to": "call_two", "type": "sequence"},
            {"from": "call_one", "to": "helper_entry", "type": "invoke"},
            {"from": "call_two", "to": "helper_entry", "type": "invoke"},
            {"from": "helper_entry", "to": "work", "type": "sequence"},
        ],
        "semanticFeatures": {
            "call_one": _features("helper"),
            "call_two": _features("helper"),
            "work": _features("helper"),
        },
    })
    analysis = analyse(graph)
    flattened = flatten_cfg(graph)

    phases = overlay_phases(analysis, flattened)

    assert len(phases) == 1
    assert _originals(flattened, phases[0].nodes) == [
        "call_one", "work", "call_two", "work"
    ]
    work_clones = [
        node_id for node_id in phases[0].nodes
        if next(node for node in flattened.nodes if node.id == node_id).origId == "work"
    ]
    assert len(work_clones) == 2
    assert work_clones[0] != work_clones[1]
    tree = overlay_phase_tree(analysis, flattened)
    assert tree["entryPoint"] == "main"
    assert tree["phases"][0]["nodes"] == phases[0].nodes
    phases[0].label = "Repeated Helper Work"
    assert phase_tree_dict(flattened, phases)["phases"][0]["label"] == (
        "Repeated Helper Work"
    )


def test_external_leaf_return_does_not_stop_caller_phase_overlay() -> None:
    graph = Graph.from_dict({
        "entryPoint": "main",
        "roots": ["main_entry"],
        "nodes": [
            {"id": "main_entry", "type": "entry", "calleeFullName": "main"},
            {"id": "first", "type": "call", "callerMethod": "main"},
            {"id": "second", "type": "call", "callerMethod": "main"},
            {"id": "first_leaf", "type": "leaf"},
            {"id": "second_leaf", "type": "leaf"},
        ],
        "edges": [
            {"from": "main_entry", "to": "first", "type": "sequence"},
            {"from": "first", "to": "second", "type": "sequence"},
            {"from": "first", "to": "first_leaf", "type": "invoke"},
            {"from": "second", "to": "second_leaf", "type": "invoke"},
        ],
        "semanticFeatures": {
            "first": _features("order"),
            "second": _features("order"),
        },
    })
    analysis = analyse(graph)
    flattened = flatten_cfg(graph)

    first_clone = next(node for node in flattened.nodes if node.origId == "first")
    first_leaf = next(node for node in flattened.nodes if node.origId == "first_leaf")
    second_clone = next(node for node in flattened.nodes if node.origId == "second")
    return_edge = next(
        edge for edge in flattened.edges
        if edge.source == first_leaf.id and edge.target == second_clone.id
    )
    assert return_edge.returnFrom == first_clone.id

    phases = overlay_phases(analysis, flattened)
    assert [_originals(flattened, phase.nodes) for phase in phases] == [[
        "first", "second"
    ]]


def test_retained_call_site_is_unphased_and_callee_phases_are_spliced() -> None:
    graph = Graph.from_dict({
        "entryPoint": "main",
        "roots": ["main_entry"],
        "nodes": [
            {"id": "main_entry", "type": "entry", "calleeFullName": "main"},
            {"id": "pre", "type": "call", "callerMethod": "main"},
            {"id": "call_helper", "type": "call", "callerMethod": "main"},
            {"id": "after", "type": "call", "callerMethod": "main"},
            {"id": "helper_entry", "type": "entry", "calleeFullName": "helper"},
            {"id": "first", "type": "call", "callerMethod": "helper"},
            {"id": "second", "type": "call", "callerMethod": "helper"},
        ],
        "edges": [
            {"from": "main_entry", "to": "pre", "type": "sequence"},
            {"from": "pre", "to": "call_helper", "type": "sequence"},
            {"from": "call_helper", "to": "after", "type": "sequence"},
            {"from": "call_helper", "to": "helper_entry", "type": "invoke"},
            {"from": "helper_entry", "to": "first", "type": "sequence"},
            {"from": "first", "to": "second", "type": "sequence"},
        ],
        "semanticFeatures": {
            "pre": _features("prepare"),
            "call_helper": _features("helper"),
            "after": _features("notify"),
            "first": _features("stock"),
            "second": _features("payment"),
        },
    })
    analysis = analyse(graph)
    labels = {
        "main_entry": ["Prepare Transfer", "Send Notification"],
        "helper_entry": ["Reserve Stock", "Take Payment"],
    }
    for entry_id, phase_labels in labels.items():
        for index, (phase, label) in enumerate(
            zip(analysis.methods[entry_id].phases, phase_labels), start=1
        ):
            phase.id = f"{entry_id}:phase:{index}"
            phase.label = label
    flattened = flatten_cfg(graph)

    phases = overlay_phases(analysis, flattened)

    assert [_originals(flattened, phase.nodes) for phase in phases] == [
        ["pre"], ["first"], ["second"], ["after"]
    ]
    assert [phase.label for phase in phases] == [
        "Prepare Transfer", "Reserve Stock", "Take Payment", "Send Notification"
    ]
    assert [phase.labelSourcePhaseId for phase in phases] == [
        "main_entry:phase:1",
        "helper_entry:phase:1",
        "helper_entry:phase:2",
        "main_entry:phase:2",
    ]
    assigned = {node_id for phase in phases for node_id in phase.nodes}
    retained_clone = next(
        node.id for node in flattened.nodes if node.origId == "call_helper"
    )
    assert retained_clone not in assigned


def test_lapsed_retention_overlays_single_phase_callee_into_caller() -> None:
    graph = Graph.from_dict({
        "entryPoint": "main",
        "roots": ["main_entry"],
        "nodes": [
            {"id": "main_entry", "type": "entry", "calleeFullName": "main"},
            {"id": "pre", "type": "call", "callerMethod": "main"},
            {"id": "call_helper", "type": "call", "callerMethod": "main"},
            {"id": "after", "type": "call", "callerMethod": "main"},
            {"id": "helper_entry", "type": "entry", "calleeFullName": "helper"},
            {"id": "first", "type": "call", "callerMethod": "helper"},
            {"id": "second", "type": "call", "callerMethod": "helper"},
        ],
        "edges": [
            {"from": "main_entry", "to": "pre", "type": "sequence"},
            {"from": "pre", "to": "call_helper", "type": "sequence"},
            {"from": "call_helper", "to": "after", "type": "sequence"},
            {"from": "call_helper", "to": "helper_entry", "type": "invoke"},
            {"from": "helper_entry", "to": "first", "type": "sequence"},
            {"from": "first", "to": "second", "type": "sequence"},
        ],
        "semanticFeatures": {
            "pre": _features("order"),
            "call_helper": _features("order"),
            "after": _features("order"),
            "first": _uncertain("first"),
            "second": _uncertain("second"),
        },
    })
    analysis = analyse(graph)
    resolve_uncertain_gates(
        analysis,
        lambda _graph, questions: {
            question.id: ("MERGE", 0.9, ()) for question in questions
        },
    )
    assert recheck_lapsed_retentions(analysis) == 1
    caller_phase = analysis.methods["main_entry"].phases[0]
    caller_phase.id = "main_entry:phase:1"
    caller_phase.label = "Complete Order"
    helper_phase = analysis.methods["helper_entry"].phases[0]
    helper_phase.id = "helper_entry:phase:1"
    helper_phase.label = "Helper Internals"
    flattened = flatten_cfg(graph)

    phases = overlay_phases(analysis, flattened)

    assert [_originals(flattened, phase.nodes) for phase in phases] == [[
        "pre", "call_helper", "first", "second", "after"
    ]]
    assert phases[0].label == "Complete Order"
    assert phases[0].labelSourcePhaseId == "main_entry:phase:1"


def test_excluded_call_suppresses_its_flattened_callee_instance() -> None:
    graph = Graph.from_dict({
        "entryPoint": "main",
        "roots": ["main_entry"],
        "nodes": [
            {"id": "main_entry", "type": "entry", "calleeFullName": "main"},
            {"id": "call_helper", "type": "call", "callerMethod": "main"},
            {"id": "helper_entry", "type": "entry", "calleeFullName": "helper"},
            {"id": "work", "type": "call", "callerMethod": "helper"},
        ],
        "edges": [
            {"from": "main_entry", "to": "call_helper", "type": "sequence"},
            {"from": "call_helper", "to": "helper_entry", "type": "invoke"},
            {"from": "helper_entry", "to": "work", "type": "sequence"},
        ],
        "semanticFeatures": {
            "call_helper": _features("helper"),
            "work": _features("helper"),
        },
    })
    analysis = analyse(graph, {"call_helper": "in-throwing-arm"})
    flattened = flatten_cfg(graph)

    assert overlay_phases(analysis, flattened) == []
