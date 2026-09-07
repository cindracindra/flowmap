"""Reusable cohesion decisions for the execution-phase abstraction."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Mapping

from domain.execution_phase.semantic import SemanticSignature


Verdict = Literal["RELATED", "UNRELATED", "UNKNOWN"]

DEFAULT_WEIGHTS: Mapping[str, float] = {
    "inputs": 0.75,
    "arguments": 0.65,
    "receivers": 0.60,
    "fields_read": 0.45,
    "method_terms": 0.30,
    "domain_types": 0.20,
}
DEFAULT_RELATED_THRESHOLD = 0.50
_CORE_IDENTITY = frozenset({"receivers", "inputs", "arguments"})


@dataclass(frozen=True, slots=True)
class SimilarityScore:
    score: float
    numerator: float
    denominator: float
    dimension_scores: tuple[tuple[str, float], ...] = ()
    evidence: tuple[str, ...] = ()
    comparable_dimensions: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CohesionDecision:
    verdict: Verdict
    confidence: float
    evidence: tuple[str, ...] = ()
    missing_evidence: tuple[str, ...] = ()
    similarity: SimilarityScore | None = None


def overlap_coefficient(left: frozenset[str], right: frozenset[str]) -> float:
    """Return intersection over the smaller non-empty set."""
    if not left or not right:
        return 0.0
    return len(left & right) / min(len(left), len(right))


def score_semantic_similarity(
    left: SemanticSignature,
    right: SemanticSignature,
    *,
    weights: Mapping[str, float] = DEFAULT_WEIGHTS,
) -> SimilarityScore:
    """Calculate weighted overlap using dimensions observed on both sides.

    Empty-empty dimensions carry no positive or negative information and are
    omitted. A dimension observed on both sides with a value on only one side
    is comparable and contributes zero, representing observed non-overlap.
    """
    left_dimensions = left.dimensions()
    right_dimensions = right.dimensions()
    numerator = 0.0
    denominator = 0.0
    dimension_scores: list[tuple[str, float]] = []
    evidence: list[str] = []
    comparable: list[str] = []

    for name, weight in weights.items():
        if weight < 0:
            raise ValueError(f"weight for {name!r} must be non-negative")
        if name not in left_dimensions or name not in right_dimensions:
            raise ValueError(f"unknown semantic dimension {name!r}")
        left_values = left_dimensions[name]
        right_values = right_dimensions[name]
        # A shared value recovered from partial extraction is valid positive
        # evidence. Non-overlap is comparable only when both sides completed
        # extraction for the category; otherwise absence is not negative.
        overlap = left_values & right_values
        completely_observed = (
            name in left.observed_features and name in right.observed_features
        )
        if not overlap and not completely_observed:
            continue
        if not left_values and not right_values:
            continue

        similarity = overlap_coefficient(left_values, right_values)
        numerator += weight * similarity
        denominator += weight
        comparable.append(name)
        dimension_scores.append((name, similarity))
        if similarity:
            evidence.append(f"{name}:{similarity:.2f}")

    return SimilarityScore(
        score=numerator / denominator if denominator else 0.0,
        numerator=numerator,
        denominator=denominator,
        dimension_scores=tuple(dimension_scores),
        evidence=tuple(evidence),
        comparable_dimensions=tuple(comparable),
    )


def _complete_and_disjoint(
    left: SemanticSignature,
    right: SemanticSignature,
) -> tuple[bool, tuple[str, ...]]:
    missing = tuple(
        sorted(
            name
            for name in _CORE_IDENTITY
            if name not in left.observed_features or name not in right.observed_features
        )
    )
    if missing:
        return False, tuple(f"observation:{name}" for name in missing)

    left_dimensions = left.dimensions()
    right_dimensions = right.dimensions()
    left_core = set().union(*(left_dimensions[name] for name in _CORE_IDENTITY))
    right_core = set().union(*(right_dimensions[name] for name in _CORE_IDENTITY))
    return bool(left_core and right_core and left_core.isdisjoint(right_core)), ()


def _contextual_decision(
    left: SemanticSignature,
    right: SemanticSignature,
    *,
    threshold: float,
    weights: Mapping[str, float],
) -> CohesionDecision:
    similarity = score_semantic_similarity(left, right, weights=weights)
    if similarity.denominator and similarity.score >= threshold:
        return CohesionDecision(
            "RELATED", similarity.score, similarity.evidence, similarity=similarity
        )
    return CohesionDecision(
        "UNKNOWN",
        similarity.score,
        similarity.evidence,
        similarity=similarity,
    )


def evaluate_op_to_op(
    left: SemanticSignature,
    right: SemanticSignature,
    *,
    direct_flow: bool = False,
    threshold: float = DEFAULT_RELATED_THRESHOLD,
    weights: Mapping[str, float] = DEFAULT_WEIGHTS,
) -> CohesionDecision:
    """Apply dependence, contradiction, then context to adjacent operations."""
    decisive: list[str] = []
    if direct_flow:
        decisive.append("direct-data-flow")
    if left.fields_written & right.fields_read:
        decisive.append("directional-write-read")
    if decisive:
        return CohesionDecision("RELATED", 1.0, tuple(decisive))

    disjoint, missing = _complete_and_disjoint(left, right)
    if disjoint:
        return CohesionDecision(
            "UNRELATED", 1.0, ("complete-core-identity-disjoint",)
        )

    decision = _contextual_decision(
        left, right, threshold=threshold, weights=weights
    )
    if decision.verdict == "UNKNOWN" and missing:
        return CohesionDecision(
            decision.verdict,
            decision.confidence,
            decision.evidence,
            missing,
            decision.similarity,
        )
    return decision


def evaluate_region_to_op(
    region: SemanticSignature,
    operation: SemanticSignature,
    *,
    threshold: float = DEFAULT_RELATED_THRESHOLD,
    weights: Mapping[str, float] = DEFAULT_WEIGHTS,
) -> CohesionDecision:
    """Evaluate an operation against an already-built core region signature."""
    disjoint, missing = _complete_and_disjoint(region, operation)
    if disjoint:
        return CohesionDecision(
            "UNRELATED", 1.0, ("complete-core-identity-disjoint",)
        )
    decision = _contextual_decision(
        region, operation, threshold=threshold, weights=weights
    )
    if decision.verdict == "UNKNOWN" and missing:
        return CohesionDecision(
            decision.verdict,
            decision.confidence,
            decision.evidence,
            missing,
            decision.similarity,
        )
    return decision


def evaluate_region_to_region(
    left: SemanticSignature,
    right: SemanticSignature,
    *,
    threshold: float = DEFAULT_RELATED_THRESHOLD,
    weights: Mapping[str, float] = DEFAULT_WEIGHTS,
) -> CohesionDecision:
    """Evaluate two core region signatures with the shared similarity model."""
    return evaluate_region_to_op(
        left, right, threshold=threshold, weights=weights
    )
