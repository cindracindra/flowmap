from __future__ import annotations

from collections.abc import Callable

import numpy as np

from model import Graph, MethodDocument, TopicAssignment, TopicCluster

from .assignment import OperationTopicClassifier, assign_operation_topics
from .discovery import discover_operation_sequences as _discover_operation_sequences


OperationLabeler = Callable[
    [dict[str, tuple[Graph, TopicCluster | None]]],
    dict[str, str | None],
]


def discover_operation_sequences(filtered_graph: Graph) -> dict[str, Graph]:
    """Discover validated operation slices from every classified CFG root."""
    return _discover_operation_sequences(filtered_graph)


def assign_operation_sequences_to_topics(
    operations: dict[str, Graph],
    clusters: list[TopicCluster],
    method_documents: list[MethodDocument],
    centroids: dict[int, np.ndarray],
    *,
    classify_fn: OperationTopicClassifier | None = None,
    model_name: str = "all-MiniLM-L6-v2",
) -> dict[str, list[TopicAssignment]]:
    """Route assignment by explicit topic provenance, never by topic labelling."""
    formed_by_llm = any(cluster.fallback for cluster in clusters)
    return assign_operation_topics(
        operations,
        clusters,
        method_documents,
        centroids,
        formed_by_llm=formed_by_llm,
        classify_fn=classify_fn,
        model_name=model_name,
    )


def label_operation_sequences(
    operations: dict[str, Graph],
    assignments: dict[str, list[TopicAssignment]],
    clusters: list[TopicCluster],
    *,
    label_fn: OperationLabeler,
) -> dict[str, str | None]:
    """Supply each operation and its assigned topic to the labelling adapter."""
    clusters_by_label = {cluster.label: cluster for cluster in clusters}
    label_inputs: dict[str, tuple[Graph, TopicCluster | None]] = {}
    for operation_id, operation in operations.items():
        operation_assignments = assignments.get(operation_id, [])
        cluster = (
            clusters_by_label.get(operation_assignments[0].label)
            if operation_assignments
            else None
        )
        label_inputs[operation_id] = (operation, cluster)
    return label_fn(label_inputs)
