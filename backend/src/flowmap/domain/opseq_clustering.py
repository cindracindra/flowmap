from __future__ import annotations

from collections.abc import Callable
import numpy as np

from model import ClassDocument, Graph, MethodDocument, TopicAssignment, TopicCluster
from domain.topic_modelling import embed_documents
from domain.util import (
    _EN_STOPS,
    _SHORT_MIN,
    split_identifier,
    preprocess_document,
)


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
    for node in operation_cfg.nodes:
        if node.type != "entry" or not node.calleeFullName:
            continue
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
    docs_by_full_name = {
        c.fullName: preprocess_document(c.terms) for c in class_documents
    }
    centroids: dict[int, np.ndarray] = {}
    for cluster in clusters:
        if cluster.label == -1:
            continue
        member_docs = [
            docs_by_full_name[fn]
            for fn in cluster.member_full_names
            if docs_by_full_name.get(fn, "").strip()
        ]
        if not member_docs:
            continue
        embeddings = embed_documents(member_docs, model_name=model_name)
        centroid = embeddings.mean(axis=0)
        centroids[cluster.label] = centroid / np.linalg.norm(centroid)
    return centroids


OperationClassifyFn = Callable[[Graph, list[TopicCluster], list[MethodDocument]], int | None]


def _assign_by_embedding(
    operation_cfg: Graph,
    clusters: list[TopicCluster],
    class_documents: list[ClassDocument],
    method_documents: list[MethodDocument],
    *,
    model_name: str,
    top_k: int,
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
    similarities.sort(key=lambda pair: pair[1], reverse=True)
    return [
        TopicAssignment(label=label, similarity=sim)
        for label, sim in similarities[:top_k]
    ]


def assign_operation_topics(
    operation_cfg: Graph,
    clusters: list[TopicCluster],
    class_documents: list[ClassDocument],
    method_documents: list[MethodDocument],
    *,
    formed_by_llm: bool = False,
    classify_fn: OperationClassifyFn | None = None,
    model_name: str = "all-MiniLM-L6-v2",
    top_k: int = 2,
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
        top_k=top_k,
    )
