from __future__ import annotations

import sys
from pathlib import Path

import pytest

FLOWMAP_SRC = Path(__file__).resolve().parents[2] / "backend" / "src" / "flowmap"
sys.path.insert(0, str(FLOWMAP_SRC))

from domain.execution_phase.cohesion import (
    CohesionDecision,
    SimilarityScore,
    evaluate_op_to_op,
    evaluate_region_to_op,
    evaluate_region_to_region,
    score_semantic_similarity,
)
from domain.execution_phase.semantic import SemanticSignature, build_core_signature
from model import NodeSemanticFeatures


OBSERVED = frozenset({"receivers", "inputs", "arguments", "fields_read"})


def signature(**values) -> SemanticSignature:
    return SemanticSignature(observed_features=OBSERVED, **values)


def test_similarity_uses_weighted_overlap_coefficient() -> None:
    left = signature(
        inputs=frozenset({"order"}),
        arguments=frozenset({"order", "customer"}),
    )
    right = signature(
        inputs=frozenset({"order", "session", "locale"}),
        arguments=frozenset({"customer", "payment"}),
    )

    result = score_semantic_similarity(
        left, right, weights={"inputs": 0.75, "arguments": 0.65}
    )

    # inputs=1/1 and arguments=1/2, then weighted and normalised.
    assert result == SimilarityScore(
        score=pytest.approx((0.75 + 0.65 * 0.5) / 1.4),
        numerator=1.075,
        denominator=1.4,
        dimension_scores=(("inputs", 1.0), ("arguments", 0.5)),
        evidence=("inputs:1.00", "arguments:0.50"),
        comparable_dimensions=("inputs", "arguments"),
    )


def test_unobserved_dimensions_are_not_negative_evidence() -> None:
    left = SemanticSignature(inputs=frozenset({"order"}))
    right = signature(inputs=frozenset({"customer"}))

    result = score_semantic_similarity(left, right, weights={"inputs": 0.75})

    assert result == SimilarityScore(0.0, 0.0, 0.0)


def test_field_read_and_write_observations_are_independent() -> None:
    read_signature = SemanticSignature.from_operation(NodeSemanticFeatures(
        fieldsRead=["status"],
        observedFeatures=["fieldsRead"],
    ))
    write_signature = SemanticSignature.from_operation(NodeSemanticFeatures(
        fieldsWritten=["status"],
        observedFeatures=["fieldsWritten"],
    ))

    assert read_signature == SemanticSignature(
        fields_read=frozenset({"status"}),
        observed_features=frozenset({"fields_read"}),
    )
    assert write_signature == SemanticSignature(
        fields_written=frozenset({"status"}),
        observed_features=frozenset({"fields_written"}),
    )


def test_extractor_field_observations_map_to_signature_dimensions() -> None:
    callsite_signature = SemanticSignature.from_operation(NodeSemanticFeatures(
        fieldsRead=["request.status"],
        observedFeatures=["callsiteFields"],
    ))
    callee_signature = SemanticSignature.from_operation(NodeSemanticFeatures(
        fieldsRead=["balance"],
        fieldsWritten=["balance"],
        observedFeatures=["calleeFields"],
    ))

    assert callsite_signature.observed_features == frozenset({"fields_read"})
    assert callee_signature.observed_features == frozenset({
        "fields_read", "fields_written",
    })

    result = score_semantic_similarity(
        callsite_signature,
        SemanticSignature.from_operation(NodeSemanticFeatures(
            fieldsRead=["request.status"],
            observedFeatures=["callsiteFields"],
        )),
        weights={"fields_read": 0.45},
    )
    assert result.score == 1.0
    assert result.comparable_dimensions == ("fields_read",)


def test_populated_partial_dimension_supports_similarity_not_disjointness() -> None:
    complete = SemanticSignature.from_operation(NodeSemanticFeatures(
        receiver="service",
        inputIdentifiers=["account"],
        observedFeatures=["receiver", "inputs"],
    ))
    partial = SemanticSignature.from_operation(NodeSemanticFeatures(
        receiver="session",
        inputIdentifiers=["account"],
        observedFeatures=["receiver"],
    ))

    assert partial.inputs == frozenset({"account"})
    assert partial.observed_features == frozenset({"receivers"})
    assert score_semantic_similarity(
        complete, partial, weights={"inputs": 0.75}
    ).score == 1.0
    assert evaluate_op_to_op(complete, partial).verdict != "UNRELATED"


def test_directional_dependence_is_decisive() -> None:
    producer = signature(fields_written=frozenset({"status"}))
    consumer = signature(fields_read=frozenset({"status"}))

    assert evaluate_op_to_op(producer, consumer) == CohesionDecision(
        "RELATED", 1.0, ("directional-write-read",)
    )
    reverse_similarity = SimilarityScore(
        0.0,
        0.0,
        0.45,
        (("fields_read", 0.0),),
        comparable_dimensions=("fields_read",),
    )
    assert evaluate_op_to_op(consumer, producer) == CohesionDecision(
        "UNKNOWN", 0.0, similarity=reverse_similarity
    )
    assert evaluate_op_to_op(signature(), signature(), direct_flow=True) == (
        CohesionDecision("RELATED", 1.0, ("direct-data-flow",))
    )


def test_complete_disjoint_identity_precedes_contextual_scoring() -> None:
    left = signature(
        receivers=frozenset({"account"}),
        inputs=frozenset({"order"}),
        fields_read=frozenset({"status"}),
    )
    right = signature(
        receivers=frozenset({"audit"}),
        inputs=frozenset({"event"}),
        fields_read=frozenset({"status"}),
    )

    decision = evaluate_op_to_op(left, right, threshold=0.1)

    assert decision == CohesionDecision(
        "UNRELATED", 1.0, ("complete-core-identity-disjoint",)
    )


def test_contextual_overlap_can_establish_related() -> None:
    left = signature(
        receivers=frozenset({"account"}), inputs=frozenset({"order"})
    )
    right = signature(
        receivers=frozenset({"account"}), inputs=frozenset({"order", "session"})
    )

    decision = evaluate_op_to_op(left, right, threshold=0.5)

    similarity = SimilarityScore(
        1.0,
        1.35,
        1.35,
        (("inputs", 1.0), ("receivers", 1.0)),
        ("inputs:1.00", "receivers:1.00"),
        ("inputs", "receivers"),
    )
    assert decision == CohesionDecision(
        "RELATED", 1.0, similarity.evidence, similarity=similarity
    )


def test_core_signature_requires_half_and_at_least_two_operations() -> None:
    region = build_core_signature((
        signature(inputs=frozenset({"order", "first-only"})),
        signature(inputs=frozenset({"order"})),
        signature(inputs=frozenset({"order"})),
    ))

    assert region == SemanticSignature(
        inputs=frozenset({"order"}),
        observed_features=OBSERVED,
        population_size=3,
    )


def test_region_evaluators_share_the_same_score() -> None:
    region = build_core_signature((
        signature(inputs=frozenset({"order"})),
        signature(inputs=frozenset({"order"})),
    ))
    operation = signature(inputs=frozenset({"order", "session"}))

    phase_decision = evaluate_region_to_op(region, operation)
    region_decision = evaluate_region_to_region(region, operation)

    similarity = SimilarityScore(
        1.0,
        0.75,
        0.75,
        (("inputs", 1.0),),
        ("inputs:1.00",),
        ("inputs",),
    )
    expected = CohesionDecision(
        "RELATED", 1.0, ("inputs:1.00",), similarity=similarity
    )
    assert phase_decision == expected
    assert region_decision == expected
