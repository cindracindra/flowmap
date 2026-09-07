from __future__ import annotations

from collections.abc import Callable

import numpy as np

from model import Graph, MethodDocument, TopicAssignment, TopicCluster

from domain.topic_discovery.embeddings import embed_documents

from .documents import build_operation_document


OperationTopicClassifier = Callable[
    [dict[str, Graph], list[TopicCluster]],
    dict[str, int | None],
]


def _assign_by_centroid(
    operations: dict[str, Graph],
    method_documents: list[MethodDocument],
    centroids: dict[int, np.ndarray],
    *,
    model_name: str,
) -> dict[str, list[TopicAssignment]]:
    assignments = {operation_id: [] for operation_id in operations}
    documents = {
        operation_id: build_operation_document(operation, method_documents)
        for operation_id, operation in operations.items()
    }
    nonempty = [
        (operation_id, document)
        for operation_id, document in documents.items()
        if document.strip()
    ]
    if not nonempty or not centroids:
        return assignments

    operation_ids, texts = zip(*nonempty)
    embeddings = embed_documents(list(texts), model_name=model_name)
    topic_labels = list(centroids)
    centroid_matrix = np.vstack([centroids[label] for label in topic_labels])
    similarities = embeddings @ centroid_matrix.T
    for row, operation_id in enumerate(operation_ids):
        best_index = int(np.argmax(similarities[row]))
        assignments[operation_id] = [
            TopicAssignment(
                label=topic_labels[best_index],
                similarity=float(similarities[row, best_index]),
            )
        ]
    return assignments


def assign_operation_topics(
    operations: dict[str, Graph],
    clusters: list[TopicCluster],
    method_documents: list[MethodDocument],
    centroids: dict[int, np.ndarray],
    *,
    formed_by_llm: bool,
    classify_fn: OperationTopicClassifier | None = None,
    model_name: str = "all-MiniLM-L6-v2",
) -> dict[str, list[TopicAssignment]]:
    """Assign operations by exactly the representation that formed the topics."""
    if formed_by_llm:
        if classify_fn is None:
            raise ValueError("LLM-formed topics require classify_fn")
        if not operations:
            return {}
        try:
            labels = classify_fn(operations, clusters)
        except RuntimeError as exc:
            print(f"assign_operation_topics: classify_fn failed ({exc!r})")
            labels = {}
        return {
            operation_id: (
                [TopicAssignment(label=label, similarity=1.0)]
                if label is not None
                else []
            )
            for operation_id in operations
            for label in [labels.get(operation_id)]
        }

    return _assign_by_centroid(
        operations,
        method_documents,
        centroids,
        model_name=model_name,
    )
