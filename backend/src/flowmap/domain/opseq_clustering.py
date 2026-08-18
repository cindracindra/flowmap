from __future__ import annotations

from collections.abc import Callable
import numpy as np

from model import ClassDocument, Graph, MethodDocument, TopicAssignment, TopicCluster
from domain.util import embed_documents
from domain.util import (
    _EN_STOPS,
    _SHORT_MIN,
    split_identifier,
    preprocess_document,
)


OperationClassifyFn = Callable[[Graph, list[TopicCluster], list[MethodDocument]], int | None]


def _entry_terms(node_callee_full_name: str, method_documents: dict[str, MethodDocument]) -> list[str]:
    """
    Term list for one "entry" node's method - rich per-method document 
    from class_document.sc's extraction when available. Falls back to 
    word-splitting the bare calleeFullName if method isn't available.
    """
    doc = method_documents.get(node_callee_full_name)
    if doc is not None:
        text = preprocess_document(doc.terms)
        if text:
            return text.split(" ")
    return [
        term
        for term in split_identifier(node_callee_full_name)
        if len(term) >= _SHORT_MIN and term not in _EN_STOPS
    ]


def build_operation_document(
    operation_cfg: Graph, method_documents: list[MethodDocument]
) -> str:
    """
    One embeddable string for a single opseq (one root's sliced+filtered CFG).
    """
    method_by_full_name = {m.fullName: m for m in method_documents}
    tokens: list[str] = []
    seen_methods: set[str] = set()
    for node in operation_cfg.nodes:
        if node.type != "entry" or not node.calleeFullName:
            continue
        if node.calleeFullName in seen_methods:
            continue
        seen_methods.add(node.calleeFullName)
        tokens.extend(_entry_terms(node.calleeFullName, method_by_full_name))
    return " ".join(tokens)


def compute_topic_centroids(
    clusters: list[TopicCluster],
    class_documents: list[ClassDocument],
    *,
    model_name: str = "all-MiniLM-L6-v2",
) -> dict[int, np.ndarray]:
    """
    Calculate mean embedding of each cluster's member classes.
    """
    docs_by_full_name = {c.fullName: preprocess_document(c.terms) for c in class_documents}
    nonempty = [(name, doc) for name, doc in docs_by_full_name.items() if doc.strip()]
    if not nonempty:
        return {}
    names, documents = zip(*nonempty)
    embedding_by_full_name = dict(
        zip(names, embed_documents(list(documents), model_name=model_name))
    )
    centroids: dict[int, np.ndarray] = {}
    for cluster in clusters:
        if cluster.label == -1:
            continue
        member_docs = [
            embedding_by_full_name[fn]
            for fn in cluster.member_full_names
            if fn in embedding_by_full_name
        ]
        if not member_docs:
            continue
        centroid = np.mean(member_docs, axis=0)
        norm = np.linalg.norm(centroid)
        if norm > 0:
            centroids[cluster.label] = centroid / norm
    return centroids


def _assign_documents_by_centroid(
    documents_by_id: dict[str, str],
    centroids: dict[int, np.ndarray],
    *,
    model_name: str,
) -> dict[str, list[TopicAssignment]]:
    """Embed all operations together and retain only each row's best topic."""
    assignments = {operation_id: [] for operation_id in documents_by_id}
    nonempty = [
        (operation_id, document)
        for operation_id, document in documents_by_id.items()
        if document.strip()
    ]
    if not nonempty or not centroids:
        return assignments

    operation_ids, documents = zip(*nonempty)
    operation_embeddings = embed_documents(list(documents), model_name=model_name)
    topic_labels = list(centroids)
    centroid_matrix = np.vstack([centroids[label] for label in topic_labels])
    similarity_matrix = operation_embeddings @ centroid_matrix.T

    for row, operation_id in enumerate(operation_ids):
        best_index = int(np.argmax(similarity_matrix[row]))
        assignments[operation_id] = [
            TopicAssignment(
                label=topic_labels[best_index],
                similarity=float(similarity_matrix[row, best_index]),
            )
        ]
    return assignments


def assign_operation_topics_batch(
    operations: dict[str, Graph],
    clusters: list[TopicCluster],
    method_documents: list[MethodDocument],
    centroids: dict[int, np.ndarray],
    *,
    formed_by_llm: bool = False,
    classify_fn: OperationClassifyFn | None = None,
    model_name: str = "all-MiniLM-L6-v2",
) -> dict[str, list[TopicAssignment]]:
    """
    Assign a complete set of opseqs. Statistical assignments share the
    discovery-time centroids and use one embedding batch for all operations.
    """
    assignments: dict[str, list[TopicAssignment]] = {}
    embedding_fallback: dict[str, Graph] = {}

    if formed_by_llm and classify_fn is not None:
        for operation_id, operation_cfg in operations.items():
            try:
                label = classify_fn(operation_cfg, clusters, method_documents)
            except RuntimeError as exc:
                print(
                    f"assign_operation_topics_batch: classify_fn failed ({exc!r}), "
                    "falling back to embedding nearest-centroid"
                )
                embedding_fallback[operation_id] = operation_cfg
            else:
                assignments[operation_id] = (
                    [TopicAssignment(label=label, similarity=1.0)]
                    if label is not None
                    else []
                )
    else:
        embedding_fallback = dict(operations)

    if embedding_fallback:
        documents = {
            operation_id: build_operation_document(operation_cfg, method_documents)
            for operation_id, operation_cfg in embedding_fallback.items()
        }
        assignments.update(
            _assign_documents_by_centroid(documents, centroids, model_name=model_name)
        )

    return {operation_id: assignments.get(operation_id, []) for operation_id in operations}


def _assign_by_embedding(
    operation_cfg: Graph,
    clusters: list[TopicCluster],
    class_documents: list[ClassDocument],
    method_documents: list[MethodDocument],
    *,
    model_name: str,
) -> list[TopicAssignment]:
    document = build_operation_document(operation_cfg, method_documents)
    if not document.strip():
        return []

    centroids = compute_topic_centroids(clusters, class_documents, model_name=model_name)
    if not centroids:
        return []

    [operation_embedding] = embed_documents([document], model_name=model_name)

    similarities = [
        (label, float(np.dot(operation_embedding, centroid)))
        for label, centroid in centroids.items()
    ]
    label, similarity = max(similarities, key=lambda pair: pair[1])
    return [TopicAssignment(label=label, similarity=similarity)]


def assign_operation_topics(
    operation_cfg: Graph,
    clusters: list[TopicCluster],
    class_documents: list[ClassDocument],
    method_documents: list[MethodDocument],
    *,
    formed_by_llm: bool = False,
    classify_fn: OperationClassifyFn | None = None,
    model_name: str = "all-MiniLM-L6-v2",
) -> list[TopicAssignment]:
    """
    Topic assignment for one opseq (a single root's filter_noise_cfg
    output). Two mechanisms, chosen by how `clusters` were themselves
    formed (LLM vs statistical).
    """
    if formed_by_llm and classify_fn is not None:
        try:
            label = classify_fn(operation_cfg, clusters, method_documents)
        except RuntimeError as exc:
            print(
                f"assign_operation_topics: classify_fn failed ({exc!r}), "
                "falling back to embedding nearest-centroid"
            )
        else:
            return [TopicAssignment(label=label, similarity=1.0)] if label is not None else []

    return _assign_by_embedding(
        operation_cfg,
        clusters,
        class_documents,
        method_documents,
        model_name=model_name,
    )
