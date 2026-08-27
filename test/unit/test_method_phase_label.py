from __future__ import annotations

import sys
from pathlib import Path

FLOWMAP_SRC = Path(__file__).resolve().parents[2] / "backend" / "src" / "flowmap"
sys.path.insert(0, str(FLOWMAP_SRC))

from domain.method_phase_label import (  # noqa: E402
    build_label_subjects,
    label_method_analysis,
)
from domain.phase_segmentation import analyse  # noqa: E402
from model import Graph  # noqa: E402


def test_builds_method_phase_evidence_without_flattening() -> None:
    graph = Graph.from_dict({
        "nodes": [
            {"id": "entry", "type": "entry", "calleeFullName": "Order.checkout:void()"},
            {
                "id": "validate", "type": "call", "callerMethod": "Order.checkout:void()",
                "calleeFullName": "Order.validate:void()", "code": "order.validate()",
            },
        ],
        "edges": [{"from": "entry", "to": "validate", "type": "sequence"}],
        "semanticFeatures": {
            "validate": {
                "receiver": "order", "domainTypes": ["Order"],
                "methodTerms": ["validate", "order"],
            },
        },
    })

    request = build_label_subjects(analyse(graph))

    assert request["schemaVersion"] == "method-phase-label-v1"
    assert len(request["subjects"]) == 1
    subject = request["subjects"][0]
    assert subject["phaseIds"] == ["entry:phase:1"]
    phase = subject["phaseEvidence"][0]
    assert phase["method"] == {"entryId": "entry", "fullName": "Order.checkout:void()"}
    assert phase["phaseIndex"] == 1
    assert phase["localPhaseCount"] == 1
    assert phase["operations"][0]["code"] == "order.validate()"
    assert phase["operations"][0]["domainTypes"] == ["Order"]


def test_groups_transitive_standalone_one_phase_delegates() -> None:
    graph = Graph.from_dict({
        "roots": ["a-entry"],
        "nodes": [
            {"id": "a-entry", "type": "entry", "calleeFullName": "A.run:void()"},
            {"id": "call-b", "type": "call", "callerMethod": "A.run:void()", "calleeFullName": "B.run:void()"},
            {"id": "b-entry", "type": "entry", "calleeFullName": "B.run:void()"},
            {"id": "call-c", "type": "call", "callerMethod": "B.run:void()", "calleeFullName": "C.run:void()"},
            {"id": "c-entry", "type": "entry", "calleeFullName": "C.run:void()"},
            {"id": "work", "type": "call", "callerMethod": "C.run:void()", "calleeFullName": "Store.save:void()"},
        ],
        "edges": [
            {"from": "a-entry", "to": "call-b", "type": "sequence"},
            {"from": "call-b", "to": "b-entry", "type": "invoke"},
            {"from": "b-entry", "to": "call-c", "type": "sequence"},
            {"from": "call-c", "to": "c-entry", "type": "invoke"},
            {"from": "c-entry", "to": "work", "type": "sequence"},
        ],
        "semanticFeatures": {
            "call-b": {"methodTerms": ["run"]},
            "call-c": {"methodTerms": ["run"]},
            "work": {"methodTerms": ["save"]},
        },
    })

    request = build_label_subjects(analyse(graph))

    assert len(request["subjects"]) == 1
    subject = request["subjects"][0]
    assert subject["id"] == "group-1"
    assert subject["phaseIds"] == [
        "a-entry:phase:1", "b-entry:phase:1", "c-entry:phase:1",
    ]
    assert [phase["method"]["entryId"] for phase in subject["phaseEvidence"]] == [
        "a-entry", "b-entry", "c-entry",
    ]


def test_does_not_group_a_call_that_joined_other_caller_operations() -> None:
    graph = Graph.from_dict({
        "roots": ["caller-entry"],
        "nodes": [
            {"id": "caller-entry", "type": "entry", "calleeFullName": "Caller.run:void()"},
            {"id": "prepare", "type": "call", "callerMethod": "Caller.run:void()", "calleeFullName": "Caller.prepare:void()"},
            {"id": "delegate", "type": "call", "callerMethod": "Caller.run:void()", "calleeFullName": "Worker.run:void()"},
            {"id": "worker-entry", "type": "entry", "calleeFullName": "Worker.run:void()"},
            {"id": "work", "type": "call", "callerMethod": "Worker.run:void()", "calleeFullName": "Store.save:void()"},
        ],
        "edges": [
            {"from": "caller-entry", "to": "prepare", "type": "sequence"},
            {"from": "prepare", "to": "delegate", "type": "sequence"},
            {"from": "delegate", "to": "worker-entry", "type": "invoke"},
            {"from": "worker-entry", "to": "work", "type": "sequence"},
        ],
        "semanticFeatures": {
            "prepare": {"methodTerms": ["work"]},
            "delegate": {"methodTerms": ["work"]},
            "work": {"methodTerms": ["work"]},
        },
    })

    analysis = analyse(graph)
    # Make the caller-side result explicit: both calls resolved into one phase.
    analysis.methods["caller-entry"].phases[0].nodes = ["prepare", "delegate"]
    analysis.methods["caller-entry"].phases = [analysis.methods["caller-entry"].phases[0]]

    request = build_label_subjects(analysis)

    assert len(request["subjects"]) == 2
    assert all(len(subject["phaseIds"]) == 1 for subject in request["subjects"])


def test_attaches_subject_label_to_every_equivalent_phase_id() -> None:
    graph = Graph.from_dict({
        "roots": ["wrapper-entry"],
        "nodes": [
            {"id": "wrapper-entry", "type": "entry", "calleeFullName": "Wrapper.reserve:void()"},
            {"id": "delegate", "type": "call", "callerMethod": "Wrapper.reserve:void()", "calleeFullName": "Ledger.reserve:void()"},
            {"id": "ledger-entry", "type": "entry", "calleeFullName": "Ledger.reserve:void()"},
            {"id": "update", "type": "call", "callerMethod": "Ledger.reserve:void()", "calleeFullName": "Store.update:void()"},
        ],
        "edges": [
            {"from": "wrapper-entry", "to": "delegate", "type": "sequence"},
            {"from": "delegate", "to": "ledger-entry", "type": "invoke"},
            {"from": "ledger-entry", "to": "update", "type": "sequence"},
        ],
        "semanticFeatures": {
            "delegate": {"methodTerms": ["reserve"]},
            "update": {"methodTerms": ["reserve", "ledger"]},
        },
    })
    analysis = analyse(graph)

    def label(request):
        assert len(request["subjects"]) == 1
        return {request["subjects"][0]["id"]: "Fund Reservation"}

    assert label_method_analysis(analysis, label) == 2
    assert [
        (phase.id, phase.label)
        for method in analysis.methods.values()
        for phase in method.phases
    ] == [
        ("ledger-entry:phase:1", "Fund Reservation"),
        ("wrapper-entry:phase:1", "Fund Reservation"),
    ]
