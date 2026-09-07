from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace

import numpy as np

from model import ClassDocument, ReadmeDocument, TopicCluster

from .clustering import cluster_embedded_corpus, embed_class_documents
from .config import FlowMapConfig
from .context import attach_readme_context, centroids_from_embeddings
from .labeling import ClusterLabelFn, apply_cluster_labels, build_clusters_with_top_terms


_MAX_NOISE_FRACTION = 0.7
_MIN_CLASSES_FOR_CLUSTERING = 20
_MAX_DF = 0.85
_TOP_N_TERMS = 10

WholeCorpusGroupingFn = Callable[
    [list[ClassDocument], list[ReadmeDocument]], list[TopicCluster]
]


def _mark_whole_corpus_origin(clusters: list[TopicCluster]) -> list[TopicCluster]:
    """Record assignment provenance independently of human-readable labels."""
    return [replace(cluster, fallback=True) for cluster in clusters]


@dataclass(slots=True)
class TopicDiscoveryResult:
    clusters: list[TopicCluster]
    centroids: dict[int, np.ndarray] = field(default_factory=dict)


def is_degenerate(clusters: list[TopicCluster], n_classes: int) -> bool:
    """Return whether local clustering should use whole-corpus fallback."""
    if n_classes < _MIN_CLASSES_FOR_CLUSTERING:
        return True
    if not any(cluster.label != -1 for cluster in clusters):
        return True
    noise = next((cluster for cluster in clusters if cluster.label == -1), None)
    noise_fraction = len(noise.member_full_names) / n_classes if noise else 0
    return noise_fraction > _MAX_NOISE_FRACTION


def _finalize_result(
    clusters: list[TopicCluster],
    class_documents: list[ClassDocument],
    readme_documents: list[ReadmeDocument],
    discovery_classes: list[ClassDocument],
    embeddings: np.ndarray,
) -> TopicDiscoveryResult:
    contextualized = attach_readme_context(
        clusters, class_documents, readme_documents
    )
    return TopicDiscoveryResult(
        clusters=contextualized,
        centroids=centroids_from_embeddings(
            contextualized, discovery_classes, embeddings
        ),
    )


def discover_topics_with_centroids(
    class_documents: list[ClassDocument],
    readme_documents: list[ReadmeDocument] | None = None,
    *,
    config: FlowMapConfig | None = None,
    label_fn: ClusterLabelFn | None = None,
    whole_corpus_fn: WholeCorpusGroupingFn | None = None,
    force_whole_corpus: bool = False,
) -> TopicDiscoveryResult:
    """Orchestrate embedding, clustering, labeling, fallback, and enrichment."""
    settings = config or FlowMapConfig()
    readmes = readme_documents or []
    corpus = embed_class_documents(class_documents)
    if not corpus.classes:
        return TopicDiscoveryResult(clusters=[])

    if force_whole_corpus:
        if whole_corpus_fn is None:
            raise ValueError("force_whole_corpus requires a whole_corpus_fn")
        return _finalize_result(
            _mark_whole_corpus_origin(whole_corpus_fn(class_documents, readmes)),
            class_documents,
            readmes,
            corpus.classes,
            corpus.embeddings,
        )

    cluster_ids = cluster_embedded_corpus(corpus, settings)
    clusters = build_clusters_with_top_terms(
        corpus.classes,
        corpus.term_texts,
        cluster_ids,
        max_df=_MAX_DF,
        top_n=_TOP_N_TERMS,
    )

    if whole_corpus_fn is not None and is_degenerate(
        clusters, len(class_documents)
    ):
        clusters = _mark_whole_corpus_origin(
            whole_corpus_fn(class_documents, readmes)
        )
    elif label_fn is not None:
        clusters = apply_cluster_labels(clusters, label_fn)

    return _finalize_result(
        clusters,
        class_documents,
        readmes,
        corpus.classes,
        corpus.embeddings,
    )
