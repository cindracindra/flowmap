from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from math import ceil
import re
from typing import Iterable, Literal

from model import Graph, NodeSemanticFeatures


RelationshipVerdict = Literal["RELATED", "UNRELATED", "UNKNOWN"]
CohesionVerdict = Literal["COMPATIBLE", "INCOMPATIBLE", "UNKNOWN"]
CandidateAction = Literal["MERGE", "SPLIT", "UNCERTAIN"]


@dataclass(frozen=True, slots=True)
class RelationshipDecision:
    verdict: RelationshipVerdict
    confidence: float
    evidence: tuple[str, ...] = ()
    missingEvidence: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PhaseSignature:
    nodeIds: tuple[str, ...]
    coreReceivers: frozenset[str] = frozenset()
    coreInputs: frozenset[str] = frozenset()
    coreArguments: frozenset[str] = frozenset()
    coreFieldsRead: frozenset[str] = frozenset()
    coreFieldsWritten: frozenset[str] = frozenset()
    coreDomainTypes: frozenset[str] = frozenset()
    coreMethodTerms: frozenset[str] = frozenset()
    coreOutputTypes: frozenset[str] = frozenset()
    observedFeatures: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class CohesionDecision:
    verdict: CohesionVerdict
    confidence: float
    evidence: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CandidateDecision:
    action: CandidateAction
    confidence: float
    local: RelationshipDecision
    cohesion: CohesionDecision
    evidence: tuple[str, ...] = ()


_SPACE = re.compile(r"\s+")
_QUOTED_LITERAL = re.compile(r"^(?:\".*\"|'.*'|[-+]?\d+(?:\.\d+)?)$")
_MINIMUM_DISJOINT_OBSERVATIONS = frozenset({"receiver", "arguments", "inputs"})
_CONTEXT_RECEIVERS = frozenset({"this", "super"})
# Frontend artefacts that carry no subject identity: Joern's placeholder for an
# expression it could not render, and the Java frontend's own temporaries. The
# extractor drops these too, but they are filtered again here so an already
# exported graph cannot merge two unrelated calls on a shared "$obj0".
_PLACEHOLDER_VALUES = frozenset({"<empty>", "<unknown>"})
_SYNTHETIC_PREFIXES = ("$",)


def _normalise(value: str) -> str:
    return _SPACE.sub("", value).strip().lower()


def _identifying(value: str) -> bool:
    return (
        value not in _PLACEHOLDER_VALUES
        and not value.startswith(_SYNTHETIC_PREFIXES)
    )


def _normalised(values: Iterable[str]) -> set[str]:
    return {
        normalised
        for normalised in (_normalise(value) for value in values if value)
        if normalised and _identifying(normalised)
    }


def _semantic_arguments(features: NodeSemanticFeatures) -> set[str]:
    """Arguments useful as semantic identity, excluding bare literals."""
    return {
        value for value in _normalised(features.arguments)
        if not _QUOTED_LITERAL.match(value)
    }


def _plain_sequence_adjacency(graph: Graph, source: str, target: str) -> bool:
    return any(
        edge.type == "sequence"
        and edge.returnFrom is None
        and edge.source == source
        and edge.target == target
        for edge in graph.edges
    )


def _direct_data_relationship(graph: Graph, left: str, right: str) -> bool:
    return any(
        edge.type == "data"
        and {edge.source, edge.target} == {left, right}
        for edge in graph.edges
    )


def _feature_sets(features: NodeSemanticFeatures) -> dict[str, set[str]]:
    receivers = _normalised([features.receiver]) if features.receiver else set()
    # `this`/`super` stay out of inputs entirely: as a receiver the caller
    # demotes them to non-scoring context, but as an input there is no such
    # demotion and every instance call in one method would share them.
    inputs = _normalised(features.inputIdentifiers) - _CONTEXT_RECEIVERS
    arguments = _semantic_arguments(features)
    reads = _normalised(features.fieldsRead)
    writes = _normalised(features.fieldsWritten)
    return {
        "receivers": receivers,
        "inputs": inputs,
        "arguments": arguments,
        "reads": reads,
        "writes": writes,
        "domains": _normalised(features.domainTypes),
        "terms": _normalised(features.methodTerms),
        "outputs": {_normalise(features.outputType)} if features.outputType else set(),
    }


def evaluate_relationship(
    graph: Graph, frontier_id: str, candidate_id: str
) -> RelationshipDecision:
    """Evaluate one immediate, method-local CFG transition.

    Positive evidence is local. Absence of a DDG edge is never negative
    evidence, and `UNRELATED` requires sufficiently complete observations on
    both nodes.
    """
    if not _plain_sequence_adjacency(graph, frontier_id, candidate_id):
        return RelationshipDecision(
            "UNKNOWN", 1.0, ("not-plain-sequence-adjacent",), ("cfg-adjacency",)
        )

    left = graph.semanticFeatures.get(frontier_id)
    right = graph.semanticFeatures.get(candidate_id)
    if left is None or right is None:
        missing = []
        if left is None:
            missing.append(f"semantic-features:{frontier_id}")
        if right is None:
            missing.append(f"semantic-features:{candidate_id}")
        return RelationshipDecision("UNKNOWN", 1.0, (), tuple(missing))

    left_sets = _feature_sets(left)
    right_sets = _feature_sets(right)
    evidence: list[str] = []
    strengths: list[float] = []

    if _direct_data_relationship(graph, frontier_id, candidate_id):
        evidence.append("direct-data-flow")
        strengths.append(1.0)
    if left_sets["inputs"] & right_sets["inputs"]:
        evidence.append("shared-input")
        strengths.append(0.95)
    if left_sets["arguments"] & right_sets["arguments"]:
        evidence.append("shared-argument")
        strengths.append(0.85)
    shared_receivers = left_sets["receivers"] & right_sets["receivers"]
    if shared_receivers - _CONTEXT_RECEIVERS:
        evidence.append("same-receiver-identity")
        strengths.append(0.8)
    elif shared_receivers:
        # `this` and `super` are common to every instance call in one method;
        # useful context, but far too broad to establish one subprocess.
        evidence.append("same-context-receiver")

    field_flow = (
        (left_sets["writes"] & (right_sets["reads"] | right_sets["writes"]))
        | (right_sets["writes"] & left_sets["reads"])
    )
    if field_flow:
        evidence.append("compatible-field-effects")
        strengths.append(0.95)
    elif left_sets["reads"] & right_sets["reads"]:
        evidence.append("shared-field-read")
        strengths.append(0.75)

    if strengths:
        return RelationshipDecision(
            "RELATED", max(strengths), tuple(evidence)
        )

    left_observed = set(left.observedFeatures)
    right_observed = set(right.observedFeatures)
    missing_categories = sorted(
        category
        for category in _MINIMUM_DISJOINT_OBSERVATIONS
        if category not in left_observed or category not in right_observed
    )
    left_footprint = set().union(*left_sets.values())
    right_footprint = set().union(*right_sets.values())
    primary_categories = ("receivers", "inputs", "arguments", "reads", "writes")
    left_primary = set().union(*(left_sets[name] for name in primary_categories))
    right_primary = set().union(*(right_sets[name] for name in primary_categories))
    if (
        not missing_categories
        and left_primary
        and right_primary
        and not left_primary & right_primary
    ):
        # Callee field effects are optional. They participate positively when
        # present, but their absence is not needed to prove that two otherwise
        # well-observed receiver/input/argument footprints are disjoint.
        return RelationshipDecision(
            "UNRELATED",
            0.75,
            ("complete-observed-footprints-are-disjoint",),
        )

    missing = [f"observation:{category}" for category in missing_categories]
    if not left_footprint:
        missing.append(f"semantic-footprint:{frontier_id}")
    if not right_footprint:
        missing.append(f"semantic-footprint:{candidate_id}")
    return RelationshipDecision("UNKNOWN", 0.6, tuple(evidence), tuple(missing))


def evaluate_region_relationship(
    graph: Graph,
    left_node_ids: Iterable[str],
    right_node_ids: Iterable[str],
) -> RelationshipDecision:
    """Combine all real CFG frontier decisions between two regions."""
    left = set(left_node_ids)
    right = set(right_node_ids)
    frontier_pairs = [
        (edge.source, edge.target)
        for edge in graph.edges
        if edge.type == "sequence"
        and edge.returnFrom is None
        and edge.source in left
        and edge.target in right
    ]
    if not frontier_pairs:
        return RelationshipDecision(
            "UNKNOWN", 1.0, ("regions-not-cfg-adjacent",), ("cfg-adjacency",)
        )

    decisions = [evaluate_relationship(graph, *pair) for pair in frontier_pairs]
    verdicts = {decision.verdict for decision in decisions}
    evidence = tuple(dict.fromkeys(
        item for decision in decisions for item in decision.evidence
    ))
    missing = tuple(dict.fromkeys(
        item for decision in decisions for item in decision.missingEvidence
    ))
    if verdicts == {"RELATED"}:
        return RelationshipDecision(
            "RELATED", min(decision.confidence for decision in decisions), evidence
        )
    if verdicts == {"UNRELATED"}:
        return RelationshipDecision(
            "UNRELATED", min(decision.confidence for decision in decisions), evidence
        )
    return RelationshipDecision(
        "UNKNOWN",
        min(decision.confidence for decision in decisions),
        evidence + ("conflicting-or-incomplete-frontiers",),
        missing,
    )



def _core_values(
    features: list[NodeSemanticFeatures],
    population_size: int,
    selector,
) -> frozenset[str]:
    counts: Counter[str] = Counter()
    for feature in features:
        counts.update(selector(_feature_sets(feature)))
    if not features or population_size == 0:
        return frozenset()
    threshold = 1 if population_size == 1 else max(2, ceil(population_size / 2))
    return frozenset(value for value, count in counts.items() if count >= threshold)


def build_phase_signature(graph: Graph, node_ids: Iterable[str]) -> PhaseSignature:
    ordered_ids = tuple(dict.fromkeys(node_ids))
    features = [
        graph.semanticFeatures[node_id]
        for node_id in ordered_ids
        if node_id in graph.semanticFeatures
    ]
    observed = frozenset(
        category for feature in features for category in feature.observedFeatures
    )
    return PhaseSignature(
        nodeIds=ordered_ids,
        coreReceivers=_core_values(
            features, len(ordered_ids), lambda values: values["receivers"]
        ),
        coreInputs=_core_values(
            features, len(ordered_ids), lambda values: values["inputs"]
        ),
        coreArguments=_core_values(
            features, len(ordered_ids), lambda values: values["arguments"]
        ),
        coreFieldsRead=_core_values(
            features, len(ordered_ids), lambda values: values["reads"]
        ),
        coreFieldsWritten=_core_values(
            features, len(ordered_ids), lambda values: values["writes"]
        ),
        coreDomainTypes=_core_values(
            features, len(ordered_ids), lambda values: values["domains"]
        ),
        coreMethodTerms=_core_values(
            features, len(ordered_ids), lambda values: values["terms"]
        ),
        coreOutputTypes=_core_values(
            features, len(ordered_ids), lambda values: values["outputs"]
        ),
        observedFeatures=observed,
    )


def evaluate_phase_cohesion(
    graph: Graph, signature: PhaseSignature, candidate_id: str
) -> CohesionDecision:
    candidate = graph.semanticFeatures.get(candidate_id)
    if candidate is None or not signature.nodeIds:
        return CohesionDecision("UNKNOWN", 1.0, ("insufficient-phase-signature",))

    values = _feature_sets(candidate)
    overlaps = {
        "phase-core-receiver": signature.coreReceivers & values["receivers"],
        "phase-core-input": signature.coreInputs & values["inputs"],
        "phase-core-argument": signature.coreArguments & values["arguments"],
        "phase-core-field": (
            signature.coreFieldsRead | signature.coreFieldsWritten
        ) & (values["reads"] | values["writes"]),
        "phase-core-domain": signature.coreDomainTypes & values["domains"],
        "phase-core-term": signature.coreMethodTerms & values["terms"],
    }
    positive = tuple(name for name, overlap in overlaps.items() if overlap)
    if positive:
        return CohesionDecision("COMPATIBLE", 0.85, positive)

    observed = set(candidate.observedFeatures)
    receiver_disjoint = (
        bool(signature.coreReceivers)
        and "receiver" in observed
        and bool(values["receivers"])
        and not signature.coreReceivers & values["receivers"]
    )
    input_disjoint = (
        bool(signature.coreInputs | signature.coreArguments)
        and {"inputs", "arguments"} <= observed
        and bool(values["inputs"] | values["arguments"])
        and not (
            (signature.coreInputs | signature.coreArguments)
            & (values["inputs"] | values["arguments"])
        )
    )
    if receiver_disjoint and input_disjoint:
        return CohesionDecision(
            "INCOMPATIBLE",
            0.8,
            ("disjoint-core-receiver", "disjoint-core-inputs"),
        )
    return CohesionDecision("UNKNOWN", 0.6, ("no-core-overlap",))


def evaluate_phase_candidate(
    graph: Graph,
    current_phase_node_ids: Iterable[str],
    frontier_id: str,
    candidate_id: str,
) -> CandidateDecision:
    """Make the local-first merge/split decision for one candidate.

    Phase-wide similarity can confirm or veto local evidence, but never turns
    an `UNKNOWN` or `UNRELATED` local transition into a merge.
    """
    phase_nodes = tuple(dict.fromkeys(current_phase_node_ids))
    if frontier_id not in phase_nodes:
        local = RelationshipDecision(
            "UNKNOWN", 1.0, ("frontier-not-in-current-phase",), ("phase-frontier",)
        )
        cohesion = CohesionDecision("UNKNOWN", 1.0, ("invalid-phase-frontier",))
        return CandidateDecision(
            "UNCERTAIN", 1.0, local, cohesion, local.evidence + cohesion.evidence
        )

    local = evaluate_relationship(graph, frontier_id, candidate_id)
    signature = build_phase_signature(graph, phase_nodes)
    cohesion = evaluate_phase_cohesion(graph, signature, candidate_id)
    evidence = tuple(dict.fromkeys((*local.evidence, *cohesion.evidence)))

    if local.verdict == "UNRELATED":
        return CandidateDecision("SPLIT", local.confidence, local, cohesion, evidence)
    if local.verdict == "RELATED" and cohesion.verdict != "INCOMPATIBLE":
        confidence = min(
            local.confidence,
            cohesion.confidence if cohesion.verdict == "COMPATIBLE" else local.confidence,
        )
        return CandidateDecision("MERGE", confidence, local, cohesion, evidence)
    # This includes RELATED+INCOMPATIBLE and every UNKNOWN local result. The
    # future LLM stage receives these cases; aggregate similarity alone is
    # deliberately insufficient to merge.
    return CandidateDecision(
        "UNCERTAIN",
        min(local.confidence, cohesion.confidence),
        local,
        cohesion,
        evidence,
    )


# Region cohesion weights, mirroring the local check's evidence hierarchy in
# `evaluate_relationship`: shared inputs are the strongest identity signal,
# receivers and arguments next, field effects below that. Domain types and
# method terms are deliberately near-worthless on their own -- measured across
# `newOrder`'s 120 region pairs, 120 share a domain type while only 4 share a
# receiver, so treating them equally collapses a whole method into one phase.
_REGION_WEIGHTS: dict[str, float] = {
    "inputs": 0.95,
    "arguments": 0.85,
    "receivers": 0.80,
    "fields": 0.75,
    "terms": 0.30,
    "domains": 0.20,
}


def _signature_dimensions(signature: PhaseSignature) -> dict[str, frozenset[str]]:
    return {
        "inputs": signature.coreInputs,
        "arguments": signature.coreArguments,
        "receivers": signature.coreReceivers - _CONTEXT_RECEIVERS,
        "fields": signature.coreFieldsRead | signature.coreFieldsWritten,
        "terms": signature.coreMethodTerms,
        "domains": signature.coreDomainTypes,
    }


def score_region_cohesion(
    graph: Graph,
    left_node_ids: Iterable[str],
    right_node_ids: Iterable[str],
) -> tuple[float, tuple[str, ...]]:
    """How much two regions share, as a weighted fraction between 0 and 1.

    Each dimension contributes its overlap ratio -- the share of the combined
    vocabulary the two sides have in common -- scaled by that dimension's weight.
    Dimensions empty on both sides are left out of the denominator entirely, so a
    pair is judged only on what could be observed of it.
    """
    left = _signature_dimensions(build_phase_signature(graph, left_node_ids))
    right = _signature_dimensions(build_phase_signature(graph, right_node_ids))

    total = 0.0
    weighted = 0.0
    evidence: list[str] = []
    for name, weight in _REGION_WEIGHTS.items():
        union = left[name] | right[name]
        if not union:
            continue
        total += weight
        ratio = len(left[name] & right[name]) / len(union)
        weighted += weight * ratio
        if ratio:
            evidence.append(f"{name}:{ratio:.2f}")

    return (weighted / total if total else 0.0), tuple(evidence)


def evaluate_region_cohesion(
    graph: Graph,
    left_node_ids: Iterable[str],
    right_node_ids: Iterable[str],
    *,
    threshold: float = 0.5,
) -> CohesionDecision:
    """Compare two whole regions, rather than a region and one operation.

    Unlike `evaluate_phase_cohesion`, which vetoes a merge that local evidence
    already supports, this decides a boundary on its own. A rule of "any overlap
    in any dimension" is safe as a veto and far too loose as a decision, so the
    verdict is a weighted score against a threshold.
    """
    left_ids = tuple(left_node_ids)
    right_ids = tuple(right_node_ids)
    if not left_ids or not right_ids:
        return CohesionDecision("UNKNOWN", 1.0, ("insufficient-region",))

    score, evidence = score_region_cohesion(graph, left_ids, right_ids)
    if score >= threshold:
        return CohesionDecision("COMPATIBLE", score, evidence)

    left = _signature_dimensions(build_phase_signature(graph, left_ids))
    right = _signature_dimensions(build_phase_signature(graph, right_ids))
    identity = ("receivers", "inputs", "arguments")
    both_observed = all(left[name] or right[name] for name in ("receivers", "inputs"))
    disjoint = all(not (left[name] & right[name]) for name in identity)
    if both_observed and disjoint:
        return CohesionDecision("INCOMPATIBLE", 1.0 - score, ("disjoint-region-identity",))
    return CohesionDecision("UNKNOWN", score, evidence or ("no-region-overlap",))

