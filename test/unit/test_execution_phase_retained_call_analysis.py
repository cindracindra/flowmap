from __future__ import annotations

from domain.execution_phase.retained_call_analysis import (
    _related_to_restored_call,
    retained_call_frontiers,
)
from domain.execution_phase.structure import LinearStructure
from domain.execution_phase.structure_analysis import (
    StraightStructureAnalysis,
)
from model import MethodDefinition, Node, NodeSemanticFeatures, Phase


def _straight(*node_ids: str) -> StraightStructureAnalysis:
    return StraightStructureAnalysis(LinearStructure(tuple(node_ids)))


def test_retained_call_frontiers_use_immediate_internal_neighbours() -> None:
    analyses = (_straight("left", "retained", "right"),)

    assert retained_call_frontiers(analyses, "retained") == (
        ("left",),
        ("right",),
    )


def test_retained_call_frontiers_walk_to_sibling_structures() -> None:
    analyses = (
        _straight("left"),
        _straight("retained"),
        _straight("right"),
    )

    assert retained_call_frontiers(analyses, "retained") == (
        ("left",),
        ("right",),
    )


def test_consecutive_retained_call_remains_a_frontier_barrier() -> None:
    analyses = (_straight("left", "retained-1", "retained-2", "right"),)

    assert retained_call_frontiers(analyses, "retained-1") == (
        ("left",),
        ("retained-2",),
    )


def test_restored_call_uses_directional_write_read_on_both_sides() -> None:
    method = MethodDefinition(
        entryId="entry",
        methodFullName="Example.run",
        entry=Node("entry", "entry", calleeFullName="Example.run"),
        semanticFeatures={
            "left": NodeSemanticFeatures(fieldsWritten=["balance"]),
            "restored": NodeSemanticFeatures(
                fieldsRead=["balance"],
                fieldsWritten=["status"],
            ),
            "right": NodeSemanticFeatures(fieldsRead=["status"]),
        },
    )

    assert _related_to_restored_call(
        method,
        Phase(nodes=["left"]),
        "left",
        "restored",
        frozenset(),
        call_is_right=True,
    )
    assert _related_to_restored_call(
        method,
        Phase(nodes=["right"]),
        "right",
        "restored",
        frozenset(),
        call_is_right=False,
    )


def test_restored_call_directional_evidence_is_frontier_only() -> None:
    method = MethodDefinition(
        entryId="entry",
        methodFullName="Example.run",
        entry=Node("entry", "entry", calleeFullName="Example.run"),
        semanticFeatures={
            "earlier": NodeSemanticFeatures(fieldsWritten=["balance"]),
            "frontier": NodeSemanticFeatures(),
            "restored": NodeSemanticFeatures(fieldsRead=["balance"]),
        },
    )

    assert not _related_to_restored_call(
        method,
        Phase(nodes=["earlier", "frontier"]),
        "frontier",
        "restored",
        frozenset({("earlier", "restored")}),
        call_is_right=True,
    )
