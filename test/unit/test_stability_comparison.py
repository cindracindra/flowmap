import pytest

from evaluation.compare_stability import (
    LabelledEntity,
    gate_pair_metrics,
    match_entities,
    operation_assignment_metrics,
    topic_partition_metrics,
)


def entity(identity: str, label: str, *members: str) -> LabelledEntity:
    return LabelledEntity(identity, label, frozenset(members))


def test_gate_pair_metrics_reports_flips_and_missing_gates() -> None:
    left = [
        {"question_id": "q1", "action": "MERGE"},
        {"question_id": "q2", "action": "SPLIT"},
    ]
    right = [
        {"question_id": "q1", "action": "SPLIT"},
        {"question_id": "q3", "action": "MERGE"},
    ]
    metrics = gate_pair_metrics(left, right)
    assert metrics["shared_gates"] == 1
    assert metrics["union_gates"] == 3
    assert metrics["gate_retention_rate"] == pytest.approx(1 / 3)
    assert metrics["flip_rate"] == 1.0
    assert metrics["merge_to_split"] == 1


def test_overlap_matching_is_one_to_one_and_ignores_numeric_identity() -> None:
    left = [entity("topic:1", "Orders", "A", "B"), entity("topic:2", "Users", "C")]
    right = [entity("topic:9", "User data", "C"), entity("topic:8", "Ordering", "A", "B")]
    matches = match_entities(left, right, minimum_overlap=0.5)
    assert [(a.identity, b.identity, score) for a, b, score in matches] == [
        ("topic:1", "topic:8", 1.0),
        ("topic:2", "topic:9", 1.0),
    ]


def test_overlap_matching_applies_minimum_threshold() -> None:
    matches = match_entities(
        [entity("a", "One", "1", "2")],
        [entity("b", "Two", "2", "3")],
        minimum_overlap=0.5,
    )
    assert matches == []


def test_exact_identity_matching_is_used_for_operations() -> None:
    matches = match_entities(
        [entity("method.A", "Read A", "method.A")],
        [entity("method.A", "Load A", "different-member")],
        minimum_overlap=1.0,
        exact_identity=True,
    )
    assert len(matches) == 1
    assert matches[0][2] == 1.0


def test_topic_partition_ari_ignores_arbitrary_topic_ids() -> None:
    metrics = topic_partition_metrics(
        {"A": 0, "B": 0, "C": 1},
        {"A": 8, "B": 8, "C": 3},
    )
    assert metrics["adjusted_rand_index"] == 1.0
    assert metrics["class_retention_rate"] == 1.0


def test_operation_assignment_agreement_uses_matched_topic_ids() -> None:
    metrics = operation_assignment_metrics(
        {"method.A": frozenset({1}), "method.B": frozenset()},
        {"method.A": frozenset({9}), "method.B": frozenset()},
        {1: 9},
    )
    assert metrics["exact_assignment_agreement"] == 1.0
    assert metrics["mean_assignment_jaccard"] == 1.0
    assert metrics["comparison_coverage"] == 1.0


def test_unmatched_topics_reduce_assignment_agreement_and_coverage() -> None:
    metrics = operation_assignment_metrics(
        {"method.A": frozenset({1})},
        {"method.A": frozenset({9})},
        {},
    )
    assert metrics["exact_assignment_agreement"] == 0.0
    assert metrics["comparison_coverage"] == 0.0
    assert metrics["mean_assignment_jaccard"] is None
