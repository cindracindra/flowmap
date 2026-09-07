from __future__ import annotations

import sys
from pathlib import Path

FLOWMAP_SRC = Path(__file__).resolve().parents[2] / "backend" / "src" / "flowmap"
sys.path.insert(0, str(FLOWMAP_SRC))

from domain.execution_phase.orchestration import (  # noqa: E402
    ExecutionPhaseAnalysis,
    build_callee_index,
    build_direct_flow_index,
    execution_phase_analysis,
)
from domain.execution_phase.method_analysis import MethodAnalysis  # noqa: E402
from domain.execution_phase.structure import LinearStructure, MethodStructure  # noqa: E402
from domain.execution_phase.structure_analysis import (  # noqa: E402
    StraightStructureAnalysis,
)
from domain.method_scoping import build_method_definitions  # noqa: E402
from execution_phase_test_support import analysis_snapshot  # noqa: E402
from model import Graph, Phase, UnresolvedGate  # noqa: E402


def _unknown_gate(left: str, right: str) -> UnresolvedGate:
    return UnresolvedGate(
        f"{left}-{right}",
        left,
        right,
        0.0,
        ("observation:arguments", "observation:inputs", "observation:receivers"),
    )


def _single_phase_analysis(entry_id: str, node_id: str) -> MethodAnalysis:
    structure = LinearStructure((node_id,))
    return MethodAnalysis(
        entry_id,
        (Phase(nodes=[node_id]),),
        structure_analyses=(StraightStructureAnalysis(
            structure, phases=[Phase(nodes=[node_id])]
        ),),
    )


def _expected_interprocedural_analysis(graph: Graph) -> ExecutionPhaseAnalysis:
    structures = {
        "caller-entry": MethodStructure(
            "caller-entry", (LinearStructure(("dispatch",)),)
        ),
        "first-entry": MethodStructure(
            "first-entry", (LinearStructure(("first-work",)),)
        ),
        "second-entry": MethodStructure(
            "second-entry", (LinearStructure(("second-work",)),)
        ),
    }
    return ExecutionPhaseAnalysis(
        graph,
        {},
        structures,
        {"dispatch": ("first-entry", "second-entry")},
        frozenset(),
        {
            "first-entry": _single_phase_analysis("first-entry", "first-work"),
            "second-entry": _single_phase_analysis("second-entry", "second-work"),
            "caller-entry": _single_phase_analysis("caller-entry", "dispatch"),
        },
        (),
    )


def _interprocedural_graph() -> Graph:
    return Graph.from_dict({
        "roots": ["caller-entry"],
        "nodes": [
            {"id": "caller-entry", "type": "entry", "calleeFullName": "Caller.run"},
            {"id": "dispatch", "type": "call", "callerMethod": "Caller.run"},
            {"id": "caller-exit", "type": "exit", "callerMethod": "Caller.run",
             "exitKind": "fallthrough"},
            {"id": "first-entry", "type": "entry", "calleeFullName": "First.run"},
            {"id": "first-work", "type": "call", "callerMethod": "First.run"},
            {"id": "first-exit", "type": "exit", "callerMethod": "First.run",
             "exitKind": "fallthrough"},
            {"id": "second-entry", "type": "entry", "calleeFullName": "Second.run"},
            {"id": "second-work", "type": "call", "callerMethod": "Second.run"},
            {"id": "second-exit", "type": "exit", "callerMethod": "Second.run",
             "exitKind": "fallthrough"},
            {"id": "external", "type": "leaf", "calleeFullName": "External.run"},
        ],
        "edges": [
            {"from": "caller-entry", "to": "dispatch", "type": "sequence"},
            {"from": "dispatch", "to": "caller-exit", "type": "sequence"},
            {"from": "dispatch", "to": "first-entry", "type": "invoke"},
            {"from": "dispatch", "to": "second-entry", "type": "invoke"},
            {"from": "dispatch", "to": "external", "type": "invoke"},
            {"from": "first-entry", "to": "first-work", "type": "sequence"},
            {"from": "first-work", "to": "first-exit", "type": "sequence"},
            {"from": "second-entry", "to": "second-work", "type": "sequence"},
            {"from": "second-work", "to": "second-exit", "type": "sequence"},
        ],
    })


def test_callee_index_retains_all_internal_polymorphic_targets() -> None:
    graph = _interprocedural_graph()
    methods = build_method_definitions(graph)

    index = build_callee_index(methods)

    assert index == {"dispatch": ("first-entry", "second-entry")}


def test_preparation_builds_exclusions_structures_and_callee_index() -> None:
    graph = _interprocedural_graph()
    methods = build_method_definitions(graph)

    preparation = execution_phase_analysis(graph, methods)

    assert preparation.graph is graph
    assert analysis_snapshot(preparation) == analysis_snapshot(
        _expected_interprocedural_analysis(graph)
    )


def test_direct_flow_index_contains_only_directional_call_data_edges() -> None:
    graph = _interprocedural_graph()
    graph.edges.extend([
        type(graph.edges[0])("first-work", "second-work", "data"),
        type(graph.edges[0])("first-entry", "second-work", "data"),
    ])

    assert build_direct_flow_index(graph) == frozenset({
        ("first-work", "second-work"),
    })


def test_preparation_does_not_call_gate_resolver_for_empty_queue() -> None:
    graph = _interprocedural_graph()
    methods = build_method_definitions(graph)

    def unexpected_resolver(questions):
        raise AssertionError("gate resolver should not be called yet")

    result = execution_phase_analysis(
        graph,
        methods,
        gate_resolver=unexpected_resolver,
    )
    assert result.graph is graph
    assert analysis_snapshot(result) == analysis_snapshot(
        _expected_interprocedural_analysis(graph)
    )


def test_preparation_resolves_uncertain_gate_and_refreshes_queue() -> None:
    graph = Graph.from_dict({
        "roots": ["entry"],
        "nodes": [
            {"id": "entry", "type": "entry", "calleeFullName": "Example.run"},
            {"id": "a", "type": "call", "callerMethod": "Example.run"},
            {"id": "b", "type": "call", "callerMethod": "Example.run"},
            {"id": "exit", "type": "exit", "callerMethod": "Example.run",
             "exitKind": "fallthrough"},
        ],
        "edges": [
            {"from": "entry", "to": "a", "type": "sequence"},
            {"from": "a", "to": "b", "type": "sequence"},
            {"from": "b", "to": "exit", "type": "sequence"},
        ],
    })
    methods = build_method_definitions(graph)
    batches = []

    def resolver(questions):
        batches.append(questions)
        return {
            question.id: ("SPLIT", 0.8, ("llm:separate",))
            for question in questions
        }

    result = execution_phase_analysis(graph, methods, resolver)

    structure = LinearStructure(("a", "b"))
    gate = _unknown_gate("a", "b")
    expected = ExecutionPhaseAnalysis(
        graph,
        {},
        {"entry": MethodStructure("entry", (structure,))},
        {},
        frozenset(),
        {"entry": MethodAnalysis(
            "entry",
            (Phase(nodes=["a"]), Phase(nodes=["b"])),
            structure_analyses=(StraightStructureAnalysis(
                structure,
                phases=[Phase(nodes=["a"]), Phase(nodes=["b"])],
                unresolved_gates=[gate],
            ),),
        )},
        (),
    )
    assert [[question.to_prompt_payload() for question in batch] for batch in batches] == [[
        {
            "gateId": "a-b",
            "method": "Example.run",
            "boundaryKind": "operation-boundary",
            "systematicAssessment": {
                "reasonCode": "INSUFFICIENT_EVIDENCE",
                "confidence": 0.0,
                "positiveEvidence": [],
                "contradictoryEvidence": [],
                "missingObservations": {
                    "left": ["arguments", "domain_types", "fields_read", "fields_written", "inputs", "method_terms", "output_types", "receivers"],
                    "right": ["arguments", "domain_types", "fields_read", "fields_written", "inputs", "method_terms", "output_types", "receivers"],
                },
                "directFlowAcrossBoundary": False,
            },
            "leftGroup": {"coreSignature": {}, "operations": [{}]},
            "rightGroup": {"coreSignature": {}, "operations": [{}]},
        }
    ]]
    assert result.graph is graph
    assert analysis_snapshot(result) == analysis_snapshot(expected)


def test_gate_resolution_rechecks_and_restores_single_phase_callee() -> None:
    graph = Graph.from_dict({
        "roots": ["outer-entry"],
        "nodes": [
            {"id": "outer-entry", "type": "entry", "calleeFullName": "Outer.run"},
            {"id": "call-inner", "type": "call", "callerMethod": "Outer.run"},
            {"id": "outer-exit", "type": "exit", "callerMethod": "Outer.run",
             "exitKind": "fallthrough"},
            {"id": "inner-entry", "type": "entry", "calleeFullName": "Inner.run"},
            {"id": "inner-a", "type": "call", "callerMethod": "Inner.run"},
            {"id": "inner-b", "type": "call", "callerMethod": "Inner.run"},
            {"id": "inner-exit", "type": "exit", "callerMethod": "Inner.run",
             "exitKind": "fallthrough"},
        ],
        "edges": [
            {"from": "outer-entry", "to": "call-inner", "type": "sequence"},
            {"from": "call-inner", "to": "outer-exit", "type": "sequence"},
            {"from": "call-inner", "to": "inner-entry", "type": "invoke"},
            {"from": "inner-entry", "to": "inner-a", "type": "sequence"},
            {"from": "inner-a", "to": "inner-b", "type": "sequence"},
            {"from": "inner-b", "to": "inner-exit", "type": "sequence"},
        ],
    })
    methods = build_method_definitions(graph)

    def resolver(questions):
        return {
            question.id: ("MERGE", 0.9, ("llm:same responsibility",))
            for question in questions
        }

    result = execution_phase_analysis(graph, methods, resolver)

    inner_structure = LinearStructure(("inner-a", "inner-b"))
    outer_structure = LinearStructure(("call-inner",))
    gate = _unknown_gate("inner-a", "inner-b")
    expected = ExecutionPhaseAnalysis(
        graph,
        {},
        {
            "outer-entry": MethodStructure("outer-entry", (outer_structure,)),
            "inner-entry": MethodStructure("inner-entry", (inner_structure,)),
        },
        {"call-inner": ("inner-entry",)},
        frozenset(),
        {
            "inner-entry": MethodAnalysis(
                "inner-entry",
                (Phase(nodes=["inner-a", "inner-b"]),),
                structure_analyses=(StraightStructureAnalysis(
                    inner_structure,
                    phases=[Phase(nodes=["inner-a"]), Phase(nodes=["inner-b"])],
                    unresolved_gates=[gate],
                ),),
            ),
            "outer-entry": MethodAnalysis(
                "outer-entry",
                (Phase(nodes=["call-inner"]),),
                structure_analyses=(StraightStructureAnalysis(
                    outer_structure,
                    retained_call_ids=["call-inner"],
                ),),
            ),
        },
        (),
    )
    assert result.graph is graph
    assert analysis_snapshot(result) == analysis_snapshot(expected)
