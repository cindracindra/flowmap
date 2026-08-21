from __future__ import annotations

import sys
from pathlib import Path

FLOWMAP_SRC = Path(__file__).resolve().parents[2] / "backend" / "src" / "flowmap"
sys.path.insert(0, str(FLOWMAP_SRC))

from domain.phase_relationship import (
    build_phase_signature,
    evaluate_phase_candidate,
    evaluate_phase_cohesion,
    evaluate_region_relationship,
    evaluate_relationship,
)
from model import Graph


OBSERVED = ["receiver", "arguments", "inputs", "callsiteFields", "output"]


def feature(
    *, receiver: str | None = None, inputs=(), arguments=(), reads=(), writes=(),
    domains=(), terms=(), observed=OBSERVED,
) -> dict:
    result = {
        "inputIdentifiers": list(inputs),
        "arguments": list(arguments),
        "fieldsRead": list(reads),
        "fieldsWritten": list(writes),
        "domainTypes": list(domains),
        "methodTerms": list(terms),
        "observedFeatures": list(observed),
    }
    if receiver is not None:
        result["receiver"] = receiver
    return result


def graph(features: dict[str, dict], *, sequence=(), data=()) -> Graph:
    node_ids = set(features)
    for source, target in (*sequence, *data):
        node_ids.update((source, target))
    return Graph.from_dict({
        "nodes": [
            {"id": node_id, "type": "call", "calleeFullName": f"Service.{node_id}"}
            for node_id in sorted(node_ids)
        ],
        "edges": [
            *({"from": source, "to": target, "type": "sequence"} for source, target in sequence),
            *({"from": source, "to": target, "type": "data"} for source, target in data),
        ],
        "semanticFeatures": features,
    })


def test_direct_data_flow_is_related() -> None:
    subject = graph(
        {"a": feature(), "b": feature()},
        sequence=(("a", "b"),),
        data=(("a", "b"),),
    )

    decision = evaluate_relationship(subject, "a", "b")

    assert decision.verdict == "RELATED"
    assert "direct-data-flow" in decision.evidence


def test_shared_input_receiver_argument_and_field_effects_are_positive_evidence() -> None:
    subject = graph(
        {
            "a": feature(
                receiver="account", inputs=("order",), arguments=("order",), writes=("status",)
            ),
            "b": feature(
                receiver="account", inputs=("order",), arguments=("order",), reads=("status",)
            ),
        },
        sequence=(("a", "b"),),
    )

    decision = evaluate_relationship(subject, "a", "b")

    assert decision.verdict == "RELATED"
    assert {
        "shared-input", "shared-argument", "same-receiver-identity",
        "compatible-field-effects",
    } <= set(decision.evidence)


def test_complete_disjoint_primary_footprints_are_unrelated() -> None:
    subject = graph(
        {
            "a": feature(receiver="account", inputs=("order",)),
            "b": feature(receiver="audit", inputs=("event",)),
        },
        sequence=(("a", "b"),),
    )

    assert evaluate_relationship(subject, "a", "b").verdict == "UNRELATED"


def test_missing_observations_are_unknown_not_unrelated() -> None:
    subject = graph(
        {
            "a": feature(receiver="account", inputs=("order",)),
            "b": feature(receiver="audit", inputs=("event",), observed=("receiver",)),
        },
        sequence=(("a", "b"),),
    )

    decision = evaluate_relationship(subject, "a", "b")

    assert decision.verdict == "UNKNOWN"
    assert "observation:arguments" in decision.missingEvidence


def test_nonadjacent_nodes_cannot_be_related_even_when_features_match() -> None:
    subject = graph({
        "a": feature(receiver="account", inputs=("order",)),
        "b": feature(receiver="account", inputs=("order",)),
    })

    decision = evaluate_relationship(subject, "a", "b")

    assert decision.verdict == "UNKNOWN"
    assert decision.evidence == ("not-plain-sequence-adjacent",)


def test_shared_this_receiver_is_context_not_proof_of_relatedness() -> None:
    subject = graph(
        {"a": feature(receiver="this"), "b": feature(receiver="this")},
        sequence=(("a", "b"),),
    )

    decision = evaluate_relationship(subject, "a", "b")

    assert decision.verdict == "UNKNOWN"
    assert "same-context-receiver" in decision.evidence


def test_region_relationship_requires_consistent_frontiers() -> None:
    subject = graph(
        {
            "a1": feature(receiver="account", inputs=("order",)),
            "a2": feature(receiver="ledger", inputs=("payment",)),
            "b1": feature(receiver="account", inputs=("order",)),
            "b2": feature(receiver="audit", inputs=("event",)),
        },
        sequence=(("a1", "b1"), ("a2", "b2")),
    )

    decision = evaluate_region_relationship(subject, ("a1", "a2"), ("b1", "b2"))

    assert decision.verdict == "UNKNOWN"
    assert "conflicting-or-incomplete-frontiers" in decision.evidence


def test_phase_signature_keeps_repeated_core_and_drops_one_off_outlier() -> None:
    subject = graph({
        "a": feature(receiver="account", inputs=("order",), terms=("validate",)),
        "b": feature(receiver="account", inputs=("order",), terms=("check",)),
        "noise": feature(receiver="logger", inputs=("message",), terms=("log",)),
    })

    signature = build_phase_signature(subject, ("a", "b", "noise"))

    assert signature.coreReceivers == frozenset({"account"})
    assert signature.coreInputs == frozenset({"order"})
    assert "logger" not in signature.coreReceivers
    assert signature.coreMethodTerms == frozenset()


def test_local_unrelated_verdict_splits_even_if_candidate_matches_distant_member() -> None:
    subject = graph(
        {
            "a": feature(receiver="account", inputs=("order",)),
            "frontier": feature(receiver="ledger", inputs=("payment",)),
            "candidate": feature(receiver="account", inputs=("order",)),
        },
        sequence=(("a", "frontier"), ("frontier", "candidate")),
    )

    decision = evaluate_phase_candidate(
        subject, ("a", "frontier"), "frontier", "candidate"
    )

    assert decision.local.verdict == "UNRELATED"
    assert decision.action == "SPLIT"


def test_local_related_but_phase_incompatible_is_uncertain() -> None:
    subject = graph(
        {
            "a": feature(receiver="account", inputs=("order",)),
            "frontier": feature(receiver="account", inputs=("order",)),
            "candidate": feature(receiver="audit", inputs=("event",)),
        },
        sequence=(("a", "frontier"), ("frontier", "candidate")),
        data=(("frontier", "candidate"),),
    )

    decision = evaluate_phase_candidate(
        subject, ("a", "frontier"), "frontier", "candidate"
    )

    assert decision.local.verdict == "RELATED"
    assert decision.cohesion.verdict == "INCOMPATIBLE"
    assert decision.action == "UNCERTAIN"


def test_phase_compatibility_cannot_turn_unknown_local_evidence_into_merge() -> None:
    subject = graph(
        {
            "a": feature(receiver="account", inputs=("order",)),
            "b": feature(receiver="account", inputs=("order",)),
            "candidate": feature(receiver="account", inputs=("order",)),
        },
        sequence=(("a", "b"), ("b", "frontier"), ("frontier", "candidate")),
    )

    decision = evaluate_phase_candidate(
        subject, ("a", "b", "frontier"), "frontier", "candidate"
    )

    assert decision.local.verdict == "UNKNOWN"
    assert decision.cohesion.verdict == "COMPATIBLE"
    assert decision.action == "UNCERTAIN"


def test_local_related_and_phase_compatible_merges() -> None:
    subject = graph(
        {
            "a": feature(receiver="account", inputs=("order",)),
            "frontier": feature(receiver="account", inputs=("order",)),
            "candidate": feature(receiver="account", inputs=("order",)),
        },
        sequence=(("a", "frontier"), ("frontier", "candidate")),
    )

    decision = evaluate_phase_candidate(
        subject, ("a", "frontier"), "frontier", "candidate"
    )

    assert decision.action == "MERGE"
    assert decision.cohesion.verdict == "COMPATIBLE"


def test_phase_cohesion_does_not_claim_incompatibility_without_complete_candidate_data() -> None:
    subject = graph({
        "a": feature(receiver="account", inputs=("order",)),
        "candidate": feature(receiver="audit", inputs=("event",), observed=("receiver",)),
    })
    signature = build_phase_signature(subject, ("a",))

    assert evaluate_phase_cohesion(subject, signature, "candidate").verdict == "UNKNOWN"


def test_frontend_placeholders_and_temporaries_are_not_shared_identity() -> None:
    # Joern renders an unreconstructable expression as "<empty>" and the Java
    # frontend mints "$obj0" temporaries. Two unrelated calls holding either
    # would otherwise produce shared-input/shared-argument -- hard positive
    # evidence -- and merge.
    subject = graph(
        {
            "a": feature(inputs=("$obj0", "this"), arguments=("<empty>",)),
            "b": feature(inputs=("$obj0", "this"), arguments=("<empty>",)),
        },
        sequence=(("a", "b"),),
    )

    decision = evaluate_relationship(subject, "a", "b")

    assert decision.verdict != "RELATED"
    assert "shared-input" not in decision.evidence
    assert "shared-argument" not in decision.evidence


def test_placeholder_only_footprint_is_unknown_not_unrelated() -> None:
    # Once the placeholders are discarded neither side has a primary footprint
    # left, so there is nothing to prove disjoint.
    subject = graph(
        {
            "a": feature(inputs=("$obj0",), arguments=("<empty>",)),
            "b": feature(inputs=("order",), arguments=("order",)),
        },
        sequence=(("a", "b"),),
    )

    assert evaluate_relationship(subject, "a", "b").verdict == "UNKNOWN"


def test_real_identifiers_still_relate_alongside_discarded_temporaries() -> None:
    subject = graph(
        {
            "a": feature(inputs=("$obj0", "order")),
            "b": feature(inputs=("$obj1", "order")),
        },
        sequence=(("a", "b"),),
    )

    decision = evaluate_relationship(subject, "a", "b")

    assert decision.verdict == "RELATED"
    assert "shared-input" in decision.evidence


def region_graph(features_by_id: dict[str, dict]) -> Graph:
    return Graph.from_dict({
        "nodes": [
            {"id": node_id, "type": "call", "calleeFullName": f"S.{node_id}"}
            for node_id in sorted(features_by_id)
        ],
        "edges": [],
        "semanticFeatures": features_by_id,
    })


def test_region_cohesion_scores_shared_identity_above_the_threshold() -> None:
    from domain.phase_relationship import evaluate_region_cohesion

    subject = region_graph({
        "a": feature(receiver="cartItem", inputs=("item",), arguments=("item",)),
        "b": feature(receiver="cartItem", inputs=("item",), arguments=("item",)),
    })

    decision = evaluate_region_cohesion(subject, ("a",), ("b",))

    assert decision.verdict == "COMPATIBLE"
    assert decision.confidence >= 0.5


def test_shared_domain_and_terms_alone_do_not_reach_the_threshold() -> None:
    # The measured failure mode: across newOrder's 120 region pairs every one
    # shares a domain type while only four share a receiver. Weighting them
    # equally collapses a whole method into a single phase.
    from domain.phase_relationship import (
        evaluate_region_cohesion,
        score_region_cohesion,
    )

    subject = region_graph({
        "a": feature(receiver="order", inputs=("order",), arguments=("order",),
                     domains=("app.Order",), terms=("get",)),
        "b": feature(receiver="session", inputs=("basket",), arguments=("basket",),
                     domains=("app.Order",), terms=("get",)),
    })

    score, evidence = score_region_cohesion(subject, ("a",), ("b",))

    assert evaluate_region_cohesion(subject, ("a",), ("b",)).verdict != "COMPATIBLE"
    assert score < 0.5
    # The overlap is real, it is simply the weakest kind.
    assert set(evidence) == {"terms:1.00", "domains:1.00"}


def test_region_cohesion_uses_partial_overlap_not_all_or_nothing() -> None:
    from domain.phase_relationship import score_region_cohesion

    subject = region_graph({
        "a": feature(inputs=("order", "cart")),
        "b": feature(inputs=("order",)),
        "c": feature(inputs=("mailer",)),
    })

    half, _ = score_region_cohesion(subject, ("a",), ("b",))
    none, _ = score_region_cohesion(subject, ("a",), ("c",))

    assert 0 < half < 1
    assert none == 0


def test_region_cohesion_ignores_dimensions_absent_on_both_sides() -> None:
    # Nothing is known about fields here, so their absence must not dilute the
    # score of two regions that agree on everything observable.
    from domain.phase_relationship import score_region_cohesion

    subject = region_graph({
        "a": feature(receiver="cart", inputs=("order",), arguments=("order",)),
        "b": feature(receiver="cart", inputs=("order",), arguments=("order",)),
    })

    score, _ = score_region_cohesion(subject, ("a",), ("b",))

    assert score == 1.0


def test_disjoint_region_identity_is_incompatible() -> None:
    from domain.phase_relationship import evaluate_region_cohesion

    subject = region_graph({
        "a": feature(receiver="cart", inputs=("order",), arguments=("order",)),
        "b": feature(receiver="mailer", inputs=("template",), arguments=("template",)),
    })

    assert evaluate_region_cohesion(subject, ("a",), ("b",)).verdict == "INCOMPATIBLE"
