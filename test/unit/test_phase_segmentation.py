from __future__ import annotations

import dataclasses
import sys
from pathlib import Path

FLOWMAP_SRC = Path(__file__).resolve().parents[2] / "backend" / "src" / "flowmap"
sys.path.insert(0, str(FLOWMAP_SRC))

from domain.phase_segmentation import (  # noqa: E402
    GateOverride,
    analyse,
    callee_count,
    materialise,
    phase_count,
)
from model import Graph  # noqa: E402


def _features(identity: str) -> dict:
    return {
        "receiver": identity,
        "inputIdentifiers": [identity],
        "arguments": [identity],
        "observedFeatures": ["receiver", "inputs", "arguments"],
    }


def test_segments_linear_structure_with_existing_relationship_rules() -> None:
    graph = Graph.from_dict({
        "roots": ["entry"],
        "nodes": [
            {"id": "entry", "type": "entry", "calleeFullName": "run"},
            {"id": "load", "type": "call", "callerMethod": "run"},
            {"id": "save", "type": "call", "callerMethod": "run"},
            {"id": "notify", "type": "call", "callerMethod": "run"},
        ],
        "edges": [
            {"from": "entry", "to": "load", "type": "sequence"},
            {"from": "load", "to": "save", "type": "sequence"},
            {"from": "save", "to": "notify", "type": "sequence"},
        ],
        "semanticFeatures": {
            "load": _features("order"),
            "save": _features("order"),
            "notify": _features("mailer"),
        },
    })

    analysis = analyse(graph)
    method = analysis.methods["entry"]

    assert [phase.nodes for phase in method.phases] == [
        ["load", "save"], ["notify"]
    ]
    assert [gate.action for gate in method.gates] == ["MERGE", "SPLIT"]


def test_callees_are_resolved_before_callers_and_multiphase_callee_is_retained() -> None:
    graph = Graph.from_dict({
        "roots": ["caller_entry"],
        "nodes": [
            {"id": "caller_entry", "type": "entry", "calleeFullName": "run"},
            {"id": "call_helper", "type": "call", "callerMethod": "run"},
            {"id": "after", "type": "call", "callerMethod": "run"},
            {"id": "helper_entry", "type": "entry", "calleeFullName": "helper"},
            {"id": "first", "type": "call", "callerMethod": "helper"},
            {"id": "second", "type": "call", "callerMethod": "helper"},
        ],
        "edges": [
            {"from": "caller_entry", "to": "call_helper", "type": "sequence"},
            {"from": "call_helper", "to": "after", "type": "sequence"},
            {"from": "call_helper", "to": "helper_entry", "type": "invoke"},
            {"from": "helper_entry", "to": "first", "type": "sequence"},
            {"from": "first", "to": "second", "type": "sequence"},
        ],
        "semanticFeatures": {
            "call_helper": _features("helper"),
            "after": _features("audit"),
            "first": _features("order"),
            "second": _features("mailer"),
        },
    })

    analysis = analyse(graph)

    assert [phase.nodes for phase in analysis.methods["helper_entry"].phases] == [
        ["first"], ["second"]
    ]
    assert [phase.nodes for phase in analysis.methods["caller_entry"].phases] == [
        ["after"]
    ]
    assert [gate.kind for gate in analysis.methods["caller_entry"].gates] == [
        "adjacency"
    ]
    assert analysis.methods["caller_entry"].retainedCallIds == {"call_helper"}
    assert phase_count(analysis, "caller_entry") == 3


def test_retained_status_does_not_change_dynamically_after_callee_merges() -> None:
    graph = Graph.from_dict({
        "roots": ["caller_entry"],
        "nodes": [
            {"id": "caller_entry", "type": "entry", "calleeFullName": "run"},
            {"id": "call_helper", "type": "call", "callerMethod": "run"},
            {"id": "after", "type": "call", "callerMethod": "run"},
            {"id": "helper_entry", "type": "entry", "calleeFullName": "helper"},
            {"id": "first", "type": "call", "callerMethod": "helper"},
            {"id": "second", "type": "call", "callerMethod": "helper"},
        ],
        "edges": [
            {"from": "caller_entry", "to": "call_helper", "type": "sequence"},
            {"from": "call_helper", "to": "after", "type": "sequence"},
            {"from": "call_helper", "to": "helper_entry", "type": "invoke"},
            {"from": "helper_entry", "to": "first", "type": "sequence"},
            {"from": "first", "to": "second", "type": "sequence"},
        ],
        "semanticFeatures": {
            "call_helper": _features("helper"),
            "after": _features("audit"),
            "first": _features("stock"),
            "second": _features("mailer"),
        },
    })
    analysis = analyse(graph)
    helper = analysis.methods["helper_entry"]
    helper.gates[0] = dataclasses.replace(
        helper.gates[0],
        override=GateOverride("MERGE", "llm", 0.9),
    )

    materialise(analysis, "helper_entry")
    assert callee_count(analysis, "call_helper") == 1

    materialise(analysis, "caller_entry")
    caller = analysis.methods["caller_entry"]
    assert caller.retainedCallIds == {"call_helper"}
    assert [phase.nodes for phase in caller.phases] == [["after"]]


def test_outer_region_collapse_skips_inner_region_check() -> None:
    graph = Graph.from_dict({
        "roots": ["entry"],
        "nodes": [
            {"id": "entry", "type": "entry", "calleeFullName": "run"},
            {"id": "pre", "type": "call", "callerMethod": "run"},
            {
                "id": "outer_work", "type": "call", "callerMethod": "run",
                "branchArms": [{"groupId": "outer", "armLabel": "if"}],
            },
            {
                "id": "inner_a", "type": "call", "callerMethod": "run",
                "branchArms": [
                    {"groupId": "outer", "armLabel": "if"},
                    {"groupId": "inner", "armLabel": "if"},
                ],
            },
            {
                "id": "inner_b", "type": "call", "callerMethod": "run",
                "branchArms": [
                    {"groupId": "outer", "armLabel": "if"},
                    {"groupId": "inner", "armLabel": "else"},
                ],
            },
            {
                "id": "outer_else", "type": "call", "callerMethod": "run",
                "branchArms": [{"groupId": "outer", "armLabel": "else"}],
            },
        ],
        "edges": [
            {"from": "entry", "to": "pre", "type": "sequence"},
            {"from": "pre", "to": "outer_work", "type": "sequence"},
            {"from": "pre", "to": "outer_else", "type": "sequence"},
            {"from": "outer_work", "to": "inner_a", "type": "sequence"},
            {"from": "outer_work", "to": "inner_b", "type": "sequence"},
        ],
        "semanticFeatures": {
            node_id: _features("cart")
            for node_id in ("pre", "outer_work", "inner_a", "inner_b", "outer_else")
        },
    })

    analysis = analyse(graph)
    method = analysis.methods["entry"]
    region_gates = [gate for gate in method.gates if gate.kind == "branch-entry"]

    gates_by_region = {gate.candidateId: gate for gate in region_gates}
    assert set(gates_by_region) == {"inner", "outer"}
    inner_gate = gates_by_region["inner"]
    outer_gate = gates_by_region["outer"]
    assert inner_gate.cohesion is None
    assert inner_gate.override is None
    assert outer_gate.override is not None
    assert outer_gate.override.decidedBy == "region"
    assert [phase.nodes for phase in method.phases] == [[
        "pre", "outer_work", "inner_a", "inner_b", "outer_else"
    ]]


def test_branch_convergence_remains_a_structural_boundary() -> None:
    graph = Graph.from_dict({
        "roots": ["entry"],
        "nodes": [
            {"id": "entry", "type": "entry", "calleeFullName": "run"},
            {"id": "pre", "type": "call", "callerMethod": "run"},
            {
                "id": "arm", "type": "call", "callerMethod": "run",
                "branchArms": [{"groupId": "g", "armLabel": "if"}],
            },
            {"id": "after", "type": "call", "callerMethod": "run"},
        ],
        "edges": [
            {"from": "entry", "to": "pre", "type": "sequence"},
            {"from": "pre", "to": "arm", "type": "sequence"},
            {"from": "arm", "to": "after", "type": "sequence"},
            {"from": "pre", "to": "after", "type": "sequence"},
        ],
        "semanticFeatures": {
            "pre": _features("order"),
            "arm": _features("stock"),
            "after": _features("audit"),
        },
    })

    method = analyse(graph).methods["entry"]

    assert [phase.nodes for phase in method.phases] == [
        ["pre"], ["arm"], ["after"]
    ]
    assert any(gate.kind == "branch-convergence" for gate in method.gates)


def test_materialise_rebuilds_phases_after_a_gate_override() -> None:
    graph = Graph.from_dict({
        "roots": ["entry"],
        "nodes": [
            {"id": "entry", "type": "entry", "calleeFullName": "run"},
            {"id": "one", "type": "call", "callerMethod": "run"},
            {"id": "two", "type": "call", "callerMethod": "run"},
        ],
        "edges": [
            {"from": "entry", "to": "one", "type": "sequence"},
            {"from": "one", "to": "two", "type": "sequence"},
        ],
        "semanticFeatures": {
            "one": _features("left"),
            "two": _features("right"),
        },
    })
    analysis = analyse(graph)
    method = analysis.methods["entry"]
    gate = method.gates[0]
    method.gates[0] = dataclasses.replace(
        gate,
        override=GateOverride("MERGE", "llm", 0.9, ("test",)),
    )

    materialise(analysis, "entry")

    assert [phase.nodes for phase in method.phases] == [["one", "two"]]


def test_self_recursive_method_phase_count_terminates() -> None:
    graph = Graph.from_dict({
        "roots": ["entry"],
        "nodes": [
            {"id": "entry", "type": "entry", "calleeFullName": "run"},
            {"id": "recursive_call", "type": "call", "callerMethod": "run"},
            {"id": "work", "type": "call", "callerMethod": "run"},
        ],
        "edges": [
            {"from": "entry", "to": "recursive_call", "type": "sequence"},
            {"from": "recursive_call", "to": "work", "type": "sequence"},
            {"from": "recursive_call", "to": "entry", "type": "invoke"},
        ],
        "semanticFeatures": {
            "recursive_call": _features("order"),
            "work": _features("order"),
        },
    })

    analysis = analyse(graph)

    assert phase_count(analysis, "entry") == 1


def test_region_collapse_does_not_cross_a_retained_callee() -> None:
    graph = Graph.from_dict({
        "roots": ["caller_entry"],
        "nodes": [
            {"id": "caller_entry", "type": "entry", "calleeFullName": "run"},
            {"id": "pre", "type": "call", "callerMethod": "run"},
            {"id": "call_helper", "type": "call", "callerMethod": "run"},
            {
                "id": "arm", "type": "call", "callerMethod": "run",
                "branchArms": [{"groupId": "g", "armLabel": "if"}],
            },
            {"id": "helper_entry", "type": "entry", "calleeFullName": "helper"},
            {"id": "first", "type": "call", "callerMethod": "helper"},
            {"id": "second", "type": "call", "callerMethod": "helper"},
        ],
        "edges": [
            {"from": "caller_entry", "to": "pre", "type": "sequence"},
            {"from": "pre", "to": "call_helper", "type": "sequence"},
            {"from": "call_helper", "to": "arm", "type": "sequence"},
            {"from": "call_helper", "to": "helper_entry", "type": "invoke"},
            {"from": "helper_entry", "to": "first", "type": "sequence"},
            {"from": "first", "to": "second", "type": "sequence"},
        ],
        "semanticFeatures": {
            "pre": _features("order"),
            "call_helper": _features("helper"),
            "arm": _features("order"),
            "first": _features("stock"),
            "second": _features("mailer"),
        },
    })

    analysis = analyse(graph)
    method = analysis.methods["caller_entry"]

    assert [phase.nodes for phase in method.phases] == [["pre"], ["arm"]]
    branch_gate = next(
        gate
        for gate in method.gates
        if gate.kind == "branch-entry" and gate.candidateId == "g"
    )
    assert branch_gate.frontierId == "call_helper"
    assert branch_gate.override is None
    assert branch_gate.cohesion is None


def test_region_at_start_can_collapse_with_phase_on_its_right() -> None:
    graph = Graph.from_dict({
        "roots": ["entry"],
        "nodes": [
            {"id": "entry", "type": "entry", "calleeFullName": "run"},
            {
                "id": "arm", "type": "call", "callerMethod": "run",
                "branchArms": [{"groupId": "g", "armLabel": "if"}],
            },
            {"id": "after", "type": "call", "callerMethod": "run"},
        ],
        "edges": [
            {"from": "entry", "to": "arm", "type": "sequence"},
            {"from": "arm", "to": "after", "type": "sequence"},
        ],
        "semanticFeatures": {
            "arm": _features("order"),
            "after": _features("order"),
        },
    })

    method = analyse(graph).methods["entry"]
    convergence = next(
        gate for gate in method.gates if gate.kind == "branch-convergence"
    )

    assert convergence.frontierId == "g"
    assert convergence.candidateId == "after"
    assert convergence.override is not None
    assert [phase.nodes for phase in method.phases] == [["arm", "after"]]


def test_back_to_back_regions_are_checked_once_by_the_left_region() -> None:
    graph = Graph.from_dict({
        "roots": ["entry"],
        "nodes": [
            {"id": "entry", "type": "entry", "calleeFullName": "run"},
            {
                "id": "first_arm", "type": "call", "callerMethod": "run",
                "branchArms": [{"groupId": "first", "armLabel": "if"}],
            },
            {
                "id": "second_arm", "type": "call", "callerMethod": "run",
                "branchArms": [{"groupId": "second", "armLabel": "if"}],
            },
        ],
        "edges": [
            {"from": "entry", "to": "first_arm", "type": "sequence"},
            {"from": "first_arm", "to": "second_arm", "type": "sequence"},
        ],
        "semanticFeatures": {
            "first_arm": _features("order"),
            "second_arm": _features("order"),
        },
    })

    method = analyse(graph).methods["entry"]
    boundary = next(
        gate
        for gate in method.gates
        if gate.kind == "branch-entry"
        and gate.frontierId == "first"
        and gate.candidateId == "second"
    )

    assert boundary.cohesion is not None
    assert boundary.override is not None
    assert [phase.nodes for phase in method.phases] == [
        ["first_arm", "second_arm"]
    ]


def test_inner_region_is_checked_when_outer_region_does_not_collapse() -> None:
    graph = Graph.from_dict({
        "roots": ["entry"],
        "nodes": [
            {"id": "entry", "type": "entry", "calleeFullName": "run"},
            {"id": "pre", "type": "call", "callerMethod": "run"},
            {
                "id": "outer_work", "type": "call", "callerMethod": "run",
                "branchArms": [{"groupId": "outer", "armLabel": "if"}],
            },
            {
                "id": "inner", "type": "call", "callerMethod": "run",
                "branchArms": [
                    {"groupId": "outer", "armLabel": "if"},
                    {"groupId": "inner", "armLabel": "if"},
                ],
            },
            {
                "id": "outer_else", "type": "call", "callerMethod": "run",
                "branchArms": [{"groupId": "outer", "armLabel": "else"}],
            },
        ],
        "edges": [
            {"from": "entry", "to": "pre", "type": "sequence"},
            {"from": "pre", "to": "outer_work", "type": "sequence"},
            {"from": "pre", "to": "outer_else", "type": "sequence"},
            {"from": "outer_work", "to": "inner", "type": "sequence"},
        ],
        "semanticFeatures": {
            "pre": _features("order"),
            "outer_work": _features("stock"),
            "inner": _features("stock"),
            "outer_else": _features("mailer"),
        },
    })

    method = analyse(graph).methods["entry"]
    outer_gate = next(
        gate for gate in method.gates
        if gate.kind == "branch-entry" and gate.candidateId == "outer"
    )
    inner_gate = next(
        gate for gate in method.gates
        if gate.kind == "branch-entry" and gate.candidateId == "inner"
    )

    assert outer_gate.override is None
    assert inner_gate.override is not None
