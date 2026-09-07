from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

import numpy as np
from sklearn.feature_extraction.text import CountVectorizer

from model import ClassDocument, TopicCluster


ClusterLabelFn = Callable[[list[TopicCluster]], dict[int, str]]


def calculate_ctfidf_scores(counts: np.ndarray) -> np.ndarray:
    """Calculate BERTopic-style class TF-IDF scores from cluster counts.

    Each row is one concatenated cluster document. Term frequency is L1
    normalized by that row's retained token count, while the inverse-frequency
    regularizer uses the integer-truncated average row length, matching
    BERTopic's ``ClassTfidfTransformer``.
    """
    cluster_lengths = counts.sum(axis=1, keepdims=True)
    term_frequency = np.divide(
        counts,
        cluster_lengths,
        out=np.zeros_like(counts, dtype=float),
        where=cluster_lengths != 0,
    )

    average_cluster_words = int(cluster_lengths.mean()) if len(counts) else 0
    term_totals = counts.sum(axis=0)
    inverse_frequency = np.log(
        1 + average_cluster_words / np.maximum(term_totals, 1)
    )
    return term_frequency * inverse_frequency


def extract_top_terms_by_cluster(
    term_texts: list[str],
    cluster_ids: np.ndarray,
    max_df: float = 0.85,
    top_n: int = 10,
) -> dict[int, list[str]]:
    """Return the highest-scoring c-TF-IDF terms for each cluster ID.

    ``term_texts`` contains one header-free evidence document per class. ``cluster_ids``
    is the aligned HDBSCAN assignment for each class. Text from classes with
    the same assignment is concatenated before c-TF-IDF is calculated.
    """
    unique_cluster_ids = sorted(int(cluster_id) for cluster_id in set(cluster_ids))
    combined_cluster_texts = [
        " ".join(
            class_text
            for class_text, assigned_cluster_id in zip(term_texts, cluster_ids)
            if int(assigned_cluster_id) == cluster_id
        )
        for cluster_id in unique_cluster_ids
    ]

    effective_max_df = max_df if len(combined_cluster_texts) >= 2 else 1.0
    vectorizer = CountVectorizer(max_df=effective_max_df)
    counts = vectorizer.fit_transform(combined_cluster_texts).toarray()
    vocabulary = vectorizer.get_feature_names_out()

    scores = calculate_ctfidf_scores(counts)

    result: dict[int, list[str]] = {}
    for row, cluster_id in enumerate(unique_cluster_ids):
        top_indices = scores[row].argsort()[::-1][:top_n]
        result[cluster_id] = [
            vocabulary[index] for index in top_indices if scores[row, index] > 0
        ]
    return result


def build_clusters_with_top_terms(
    classes: list[ClassDocument],
    term_texts: list[str],
    cluster_ids: np.ndarray,
    *,
    max_df: float = 0.85,
    top_n: int = 10,
) -> list[TopicCluster]:
    """Build topic clusters from assignments and their c-TF-IDF top terms."""
    top_terms_by_cluster = extract_top_terms_by_cluster(
        term_texts, cluster_ids, max_df=max_df, top_n=top_n
    )
    return [
        TopicCluster(
            label=cluster_id,
            member_full_names=[
                class_document.fullName
                for class_document, assigned_cluster_id in zip(classes, cluster_ids)
                if int(assigned_cluster_id) == cluster_id
            ],
            statistical_terms=top_terms_by_cluster.get(cluster_id, []),
        )
        for cluster_id in sorted(int(cluster_id) for cluster_id in set(cluster_ids))
    ]


def apply_cluster_labels(
    clusters: list[TopicCluster], label_fn: ClusterLabelFn
) -> list[TopicCluster]:
    """Apply semantic labels without coupling them to clustering."""
    if not clusters:
        return []
    labels = label_fn(clusters)
    return [
        replace(
            cluster,
            llm_label=labels.get(
                cluster.label,
                cluster.statistical_terms[0]
                if cluster.statistical_terms else "unlabeled",
            ),
        )
        for cluster in clusters
    ]
