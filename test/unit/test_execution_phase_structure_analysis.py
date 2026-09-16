from __future__ import annotations

from domain.execution_phase.structure import (
    BranchStructure,
    LinearStructure,
)
from domain.execution_phase.structure_analysis import (
    BranchStructureAnalysis,
    StraightStructureAnalysis,
    analyse_branch_structure,
    analyse_straight_structure,
)
from execution_phase_test_support import structure_analysis_snapshot
from model import (
    MethodDefinition,
    Node,
    NodeSemanticFeatures,
    Phase,
    UnresolvedGate,
)


def _method() -> MethodDefinition:
    return MethodDefinition(
        entryId="entry",
        methodFullName="Example.run",
        entry=Node("entry", "entry", calleeFullName="Example.run"),
    )


def _identity_features(value: str) -> NodeSemanticFeatures:
    return NodeSemanticFeatures(
        receiver=value,
        inputIdentifiers=[value],
        arguments=[value],
        observedFeatures=["receiver", "inputs", "arguments"],
    )


def test_straight_analysis_splits_definitely_unrelated_calls_without_a_gate() -> None:
    method = _method()
    method.semanticFeatures = {
        "a": _identity_features("order"),
        "b": _identity_features("invoice"),
    }

    result = analyse_straight_structure(
        method, LinearStructure(("a", "b")), frozenset(), frozenset()
    )

    expected = StraightStructureAnalysis(
        LinearStructure(("a", "b")),
        phases=[Phase(nodes=["a"]), Phase(nodes=["b"])],
    )
    assert structure_analysis_snapshot(result) == structure_analysis_snapshot(expected)


def test_straight_analysis_merges_local_related_when_region_is_unknown() -> None:
    method = _method()
    method.semanticFeatures = {"a": _identity_features("order")}

    result = analyse_straight_structure(
        method,
        LinearStructure(("a", "b")),
        frozenset(),
        frozenset({("a", "b")}),
    )

    expected = StraightStructureAnalysis(
        LinearStructure(("a", "b")),
        phases=[Phase(nodes=["a", "b"])],
    )
    assert structure_analysis_snapshot(result) == structure_analysis_snapshot(expected)


def test_directional_flow_is_not_vetoed_by_aggregate_identity() -> None:
    method = _method()
    method.semanticFeatures = {
        "a": _identity_features("order"),
        "b": _identity_features("order"),
        "c": _identity_features("invoice"),
    }

    result = analyse_straight_structure(
        method,
        LinearStructure(("a", "b", "c")),
        frozenset(),
        frozenset({("b", "c")}),
    )

    expected = StraightStructureAnalysis(
        LinearStructure(("a", "b", "c")),
        phases=[Phase(nodes=["a", "b", "c"])],
    )
    assert structure_analysis_snapshot(result) == structure_analysis_snapshot(expected)


def test_straight_analysis_preserves_unknown_local_boundary_for_resolution() -> None:
    result = analyse_straight_structure(
        _method(), LinearStructure(("a", "b")), frozenset(), frozenset()
    )

    expected = StraightStructureAnalysis(
        LinearStructure(("a", "b")),
        phases=[Phase(nodes=["a"]), Phase(nodes=["b"])],
        unresolved_gates=[UnresolvedGate(
            "a-b",
            "a",
            "b",
            0.0,
            ("observation:arguments", "observation:inputs", "observation:receivers"),
        )],
    )
    assert structure_analysis_snapshot(result) == structure_analysis_snapshot(expected)


def test_straight_analysis_records_consecutive_retained_calls_in_order() -> None:
    result = analyse_straight_structure(
        _method(),
        LinearStructure(("a", "retained-1", "retained-2", "b")),
        frozenset({"retained-1", "retained-2"}),
        frozenset(),
    )

    expected = StraightStructureAnalysis(
        LinearStructure(("a", "retained-1", "retained-2", "b")),
        phases=[Phase(nodes=["a"]), Phase(nodes=["b"])],
        retained_call_ids=["retained-1", "retained-2"],
    )
    assert structure_analysis_snapshot(result) == structure_analysis_snapshot(expected)


def test_branch_analysis_recursively_dispatches_each_arm_independently() -> None:
    nested = BranchStructure(
        "inner",
        (
            (LinearStructure(("inner-a",)),),
            (LinearStructure(("inner-b",)),),
        ),
    )
    branch = BranchStructure(
        "outer",
        (
            (LinearStructure(("outer-a",)), nested),
            (LinearStructure(("outer-b",)),),
        ),
    )
    result = analyse_branch_structure(
        _method(),
        branch,
        frozenset({"inner-b"}),
        frozenset({("outer-a", "inner-a")}),
    )

    outer_a = StraightStructureAnalysis(
        LinearStructure(("outer-a",)), phases=[Phase(nodes=["outer-a"])]
    )
    inner_a = StraightStructureAnalysis(
        LinearStructure(("inner-a",)), phases=[Phase(nodes=["inner-a"])]
    )
    inner_b = StraightStructureAnalysis(
        LinearStructure(("inner-b",)), retained_call_ids=["inner-b"]
    )
    inner_result = BranchStructureAnalysis(
        nested,
        ((inner_a,), (inner_b,)),
        phases=(inner_a.phases[0],),
        retained_call_ids=("inner-b",),
    )
    outer_b = StraightStructureAnalysis(
        LinearStructure(("outer-b",)), phases=[Phase(nodes=["outer-b"])]
    )
    expected = BranchStructureAnalysis(
        branch,
        ((outer_a, inner_result), (outer_b,)),
        phases=(outer_a.phases[0], inner_a.phases[0], outer_b.phases[0]),
        retained_call_ids=("inner-b",),
    )
    assert structure_analysis_snapshot(result) == structure_analysis_snapshot(expected)
