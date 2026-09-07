from __future__ import annotations

import sys
from pathlib import Path

FLOWMAP_SRC = Path(__file__).resolve().parents[2] / "backend" / "src" / "flowmap"
sys.path.insert(0, str(FLOWMAP_SRC))

from domain.execution_phase.method_analysis import (  # noqa: E402
    MethodAnalysis,
    analyse_method,
    effective_phase_count,
    resolve,
)
from domain.execution_phase.structure import LinearStructure, MethodStructure  # noqa: E402
from domain.execution_phase.structure_analysis import (  # noqa: E402
    StraightStructureAnalysis,
)
from domain.execution_phase.orchestration import (  # noqa: E402
    execution_phase_analysis,
)
from domain.method_scoping import build_method_definitions  # noqa: E402
from execution_phase_test_support import method_analysis_snapshot  # noqa: E402
from model import (  # noqa: E402
    Graph,
    MethodDefinition,
    Node,
    NodeSemanticFeatures,
    Phase,
    UnresolvedGate,
)


def _unknown_gate(left: str, right: str) -> UnresolvedGate:
    return UnresolvedGate(
        f"{left}-{right}",
        left,
        right,
        0.0,
        ("observation:arguments", "observation:inputs", "observation:receivers"),
    )


def _chain_graph() -> Graph:
    return Graph.from_dict({
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


def test_analyse_method_aggregates_linear_structure_results() -> None:
    graph = _chain_graph()
    methods = build_method_definitions(graph)
    preparation = execution_phase_analysis(graph, methods)

    result = analyse_method(
        methods["inner-entry"],
        preparation.structures_by_entry_id["inner-entry"],
        frozenset(),
        preparation.direct_flow_pairs,
    )

    gate = _unknown_gate("inner-a", "inner-b")
    linear = LinearStructure(("inner-a", "inner-b"))
    expected = MethodAnalysis(
        "inner-entry",
        (Phase(nodes=["inner-a"]), Phase(nodes=["inner-b"])),
        unresolved_gates=(gate,),
        structure_analyses=(StraightStructureAnalysis(
            linear,
            phases=[Phase(nodes=["inner-a"]), Phase(nodes=["inner-b"])],
            unresolved_gates=[gate],
        ),),
    )
    assert method_analysis_snapshot(result) == method_analysis_snapshot(expected)


def test_method_analysis_merges_related_sibling_regions() -> None:
    method = MethodDefinition(
        entryId="entry",
        methodFullName="Example.run",
        entry=Node("entry", "entry", calleeFullName="Example.run"),
        semanticFeatures={
            node_id: NodeSemanticFeatures(
                receiver="order",
                observedFeatures=["receiver"],
            )
            for node_id in ("a", "b")
        },
    )
    structure = MethodStructure(
        "entry",
        (LinearStructure(("a",)), LinearStructure(("b",))),
    )

    result = analyse_method(method, structure, frozenset(), frozenset())

    left = StraightStructureAnalysis(
        structure.structures[0], phases=[Phase(nodes=["a", "b"])]
    )
    right = StraightStructureAnalysis(
        structure.structures[1], phases=[Phase(nodes=["b"])]
    )
    expected = MethodAnalysis(
        "entry",
        (Phase(nodes=["a", "b"]),),
        structure_analyses=(left, right),
    )
    assert method_analysis_snapshot(result) == method_analysis_snapshot(expected)


def test_method_analysis_does_not_compare_across_retained_frontier() -> None:
    method = MethodDefinition(
        entryId="entry",
        methodFullName="Example.run",
        entry=Node("entry", "entry", calleeFullName="Example.run"),
    )
    structure = MethodStructure(
        "entry",
        (
            LinearStructure(("a", "retained")),
            LinearStructure(("b",)),
        ),
    )

    result = analyse_method(
        method,
        structure,
        frozenset({"retained"}),
        frozenset(),
    )

    expected = MethodAnalysis(
        "entry",
        (Phase(nodes=["a"]), Phase(nodes=["b"])),
        frozenset({"retained"}),
        structure_analyses=(
            StraightStructureAnalysis(
                structure.structures[0],
                phases=[Phase(nodes=["a"])],
                retained_call_ids=["retained"],
            ),
            StraightStructureAnalysis(
                structure.structures[1], phases=[Phase(nodes=["b"])]
            ),
        ),
    )
    assert method_analysis_snapshot(result) == method_analysis_snapshot(expected)


def test_method_analysis_uses_direct_flow_across_structure_boundary() -> None:
    method = MethodDefinition(
        entryId="entry",
        methodFullName="Example.run",
        entry=Node("entry", "entry", calleeFullName="Example.run"),
    )
    structure = MethodStructure(
        "entry",
        (LinearStructure(("a",)), LinearStructure(("b",))),
    )
    method.semanticFeatures = {
        "a": NodeSemanticFeatures(
            receiver="order",
            inputIdentifiers=["order"],
            arguments=["order"],
            observedFeatures=["receiver", "inputs", "arguments"],
        ),
        "b": NodeSemanticFeatures(
            receiver="invoice",
            inputIdentifiers=["invoice"],
            arguments=["invoice"],
            observedFeatures=["receiver", "inputs", "arguments"],
        ),
    }

    result = analyse_method(
        method,
        structure,
        frozenset(),
        frozenset({("a", "b")}),
    )

    expected = MethodAnalysis(
        "entry",
        (Phase(nodes=["a", "b"]),),
        structure_analyses=(
            StraightStructureAnalysis(
                structure.structures[0], phases=[Phase(nodes=["a", "b"])]
            ),
            StraightStructureAnalysis(
                structure.structures[1], phases=[Phase(nodes=["b"])]
            ),
        ),
    )
    assert method_analysis_snapshot(result) == method_analysis_snapshot(expected)


def test_method_analysis_uses_write_read_across_structure_boundary() -> None:
    method = MethodDefinition(
        entryId="entry",
        methodFullName="Example.run",
        entry=Node("entry", "entry", calleeFullName="Example.run"),
        semanticFeatures={
            "a": NodeSemanticFeatures(
                receiver="account",
                inputIdentifiers=["account"],
                arguments=["account"],
                fieldsWritten=["balance"],
                observedFeatures=[
                    "receiver",
                    "inputs",
                    "arguments",
                    "calleeFields",
                ],
            ),
            "b": NodeSemanticFeatures(
                receiver="ledger",
                inputIdentifiers=["ledger"],
                arguments=["ledger"],
                fieldsRead=["balance"],
                observedFeatures=[
                    "receiver",
                    "inputs",
                    "arguments",
                    "calleeFields",
                ],
            ),
        },
    )
    structure = MethodStructure(
        "entry",
        (LinearStructure(("a",)), LinearStructure(("b",))),
    )

    result = analyse_method(method, structure, frozenset(), frozenset())

    assert tuple(phase.nodes for phase in result.phases) == (["a", "b"],)


def test_structure_boundary_directional_evidence_is_frontier_only() -> None:
    method = MethodDefinition(
        entryId="entry",
        methodFullName="Example.run",
        entry=Node("entry", "entry", calleeFullName="Example.run"),
        semanticFeatures={
            "a": NodeSemanticFeatures(
                receiver="order",
                fieldsWritten=["balance"],
                observedFeatures=["receiver", "calleeFields"],
            ),
            "b": NodeSemanticFeatures(
                receiver="order",
                observedFeatures=["receiver"],
            ),
            "c": NodeSemanticFeatures(fieldsRead=["balance"]),
        },
    )
    structure = MethodStructure(
        "entry",
        (LinearStructure(("a", "b")), LinearStructure(("c",))),
    )

    result = analyse_method(
        method,
        structure,
        frozenset(),
        frozenset({("a", "c")}),
    )

    assert tuple(phase.nodes for phase in result.phases) == (["a", "b"], ["c"])
    assert len(result.unresolved_gates) == 1


def test_resolve_retains_call_to_multi_phase_callee() -> None:
    graph = _chain_graph()
    methods = build_method_definitions(graph)
    preparation = execution_phase_analysis(graph, methods)

    analyses: dict[str, MethodAnalysis] = {}
    result = resolve(
        "outer-entry",
        methods_by_entry_id=methods,
        structures_by_entry_id=preparation.structures_by_entry_id,
        callee_entries_by_call_id=preparation.callee_entries_by_call_id,
        direct_flow_pairs=preparation.direct_flow_pairs,
        analyses_by_entry_id=analyses,
        resolving_entry_ids=set(),
    )

    gate = _unknown_gate("inner-a", "inner-b")
    inner_structure = preparation.structures_by_entry_id["inner-entry"].structures[0]
    outer_structure = preparation.structures_by_entry_id["outer-entry"].structures[0]
    expected = {
        "inner-entry": MethodAnalysis(
            "inner-entry",
            (Phase(nodes=["inner-a"]), Phase(nodes=["inner-b"])),
            unresolved_gates=(gate,),
            structure_analyses=(StraightStructureAnalysis(
                inner_structure,
                phases=[Phase(nodes=["inner-a"]), Phase(nodes=["inner-b"])],
                unresolved_gates=[gate],
            ),),
        ),
        "outer-entry": MethodAnalysis(
            "outer-entry",
            retained_call_ids=frozenset({"call-inner"}),
            structure_analyses=(StraightStructureAnalysis(
                outer_structure,
                retained_call_ids=["call-inner"],
            ),),
        ),
    }
    assert result is analyses["outer-entry"]
    assert {
        entry_id: method_analysis_snapshot(analysis)
        for entry_id, analysis in analyses.items()
    } == {
        entry_id: method_analysis_snapshot(analysis)
        for entry_id, analysis in expected.items()
    }


def test_effective_phase_count_includes_retained_callee_phases() -> None:
    analyses = {
        "outer": MethodAnalysis(
            "outer", (Phase(nodes=["a"]),), frozenset({"call-inner"})
        ),
        "inner": MethodAnalysis(
            "inner", (Phase(nodes=["b"]), Phase(nodes=["c"])), frozenset()
        ),
    }

    assert effective_phase_count(
        "outer", analyses, {"call-inner": ("inner",)}
    ) == 3


def test_recursive_reentry_is_retained_without_recursing_forever() -> None:
    graph = Graph.from_dict({
        "roots": ["entry"],
        "nodes": [
            {"id": "entry", "type": "entry", "calleeFullName": "Recursive.run"},
            {"id": "self-call", "type": "call", "callerMethod": "Recursive.run"},
            {"id": "exit", "type": "exit", "callerMethod": "Recursive.run",
             "exitKind": "fallthrough"},
        ],
        "edges": [
            {"from": "entry", "to": "self-call", "type": "sequence"},
            {"from": "self-call", "to": "exit", "type": "sequence"},
            {"from": "self-call", "to": "entry", "type": "invoke"},
        ],
    })
    methods = build_method_definitions(graph)
    preparation = execution_phase_analysis(graph, methods)

    result = resolve(
        "entry",
        methods_by_entry_id=methods,
        structures_by_entry_id=preparation.structures_by_entry_id,
        callee_entries_by_call_id=preparation.callee_entries_by_call_id,
        direct_flow_pairs=preparation.direct_flow_pairs,
        analyses_by_entry_id={},
        resolving_entry_ids=set(),
    )

    expected = MethodAnalysis(
        "entry",
        retained_call_ids=frozenset({"self-call"}),
        structure_analyses=(StraightStructureAnalysis(
            preparation.structures_by_entry_id["entry"].structures[0],
            retained_call_ids=["self-call"],
        ),),
    )
    assert result is not None
    assert method_analysis_snapshot(result) == method_analysis_snapshot(expected)
