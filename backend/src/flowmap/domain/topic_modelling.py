from __future__ import annotations

import dataclasses
import numpy as np
from dataclasses import dataclass, field
from collections.abc import Callable
from pathlib import Path

from sklearn.cluster import HDBSCAN
from sklearn.feature_extraction.text import CountVectorizer

from model import ClassDocument, ReadmeDocument, TopicCluster
from domain.util import embed_documents, preprocess_document

_MAX_NOISE_FRACTION = 0.7  # >70% of classes in noise cluster is degenerate
_MIN_CLASSES_FOR_CLUSTERING = 20  # below this, HDBSCAN has too few points to be meaningful


@dataclass(slots=True)
class TopicDiscoveryResult:
    """Topic metadata plus the centroids derived from discovery embeddings."""

    clusters: list[TopicCluster]
    centroids: dict[int, np.ndarray] = field(default_factory=dict)


def _centroids_from_discovery_embeddings(
    clusters: list[TopicCluster],
    classes: list[ClassDocument],
    embeddings: np.ndarray,
) -> dict[int, np.ndarray]:
    """Mean each final cluster's already-computed class embeddings."""
    embedding_by_full_name = {
        class_doc.fullName: embedding
        for class_doc, embedding in zip(classes, embeddings)
    }
    centroids: dict[int, np.ndarray] = {}
    for cluster in clusters:
        if cluster.label == -1:
            continue
        members = [
            embedding_by_full_name[full_name]
            for full_name in cluster.member_full_names
            if full_name in embedding_by_full_name
        ]
        if not members:
            continue
        centroid = np.mean(members, axis=0)
        norm = np.linalg.norm(centroid)
        if norm > 0:
            centroids[cluster.label] = centroid / norm
    return centroids


def _is_within(candidate_dir: Path, base_dir: Path) -> bool:
    """True if candidate_dir is base_dir itself or nested under it."""
    return candidate_dir == base_dir or base_dir in candidate_dir.parents


def _common_package_prefix(packages: list[str]) -> str:
    """Longest dotted-package prefix shared by every package in `packages`."""
    if not packages:
        return ""
    split = [p.split(".") if p else [] for p in packages]
    prefix: list[str] = []
    for parts in zip(*split):
        if len(set(parts)) != 1:
            break
        prefix.append(parts[0])
    return ".".join(prefix)


def extract_readme_documents(
    source_root: Path, class_documents: list[ClassDocument]
) -> list[ReadmeDocument]:
    """
    Filesystem walk for README*/*.md under source_root. Each doc is mapped 
    to the nearest enclosing Java package.
    """
    class_dirs = [(Path(c.filename).parent, c.package) for c in class_documents]
    docs: list[ReadmeDocument] = []
    for path in sorted(source_root.rglob("*")):
        if not path.is_file():
            continue
        name = path.name
        if not (name.upper().startswith("README") or name.lower().endswith(".md")):
            continue
        rel_dir = path.parent.relative_to(source_root)
        candidates = [pkg for cdir, pkg in class_dirs if _is_within(cdir, rel_dir)]
        docs.append(
            ReadmeDocument(
                path=str(path.relative_to(source_root)),
                package=_common_package_prefix(candidates),
                text=path.read_text(errors="ignore"),
            )
        )
    return docs


def cluster_documents(embeddings: np.ndarray, min_cluster_size: int = 3) -> np.ndarray:
    """
    HDBSCAN over the embedding vectors -- no upfront topic count, and a
    class that doesn't fit any feature group lands in the noise bucket
    (label -1).
    """
    if len(embeddings) < 2:
        return np.full(len(embeddings), -1)
    return HDBSCAN(
        min_cluster_size=min_cluster_size, metric="euclidean", copy=False
    ).fit_predict(embeddings)


def label_clusters_statistical(
    docs: list[str], labels: np.ndarray, max_df: float = 0.85, top_n: int = 10
) -> dict[int, list[str]]:
    """
    Class-based TF-IDF (c-TF-IDF) top terms per cluster, computed locally
    from CountVectorizer: each cluster's member docs are concatenated
    into one mega-document, counted, then weighted by idf(t) = log(1 + A / f_t), 
    where f_t is term t's total count across ALL clusters and A is the 
    average word count per cluster.
    """
    unique_labels = sorted(int(lbl) for lbl in set(labels))
    mega_docs = [
        " ".join(doc for doc, lbl in zip(docs, labels) if int(lbl) == cluster_id)
        for cluster_id in unique_labels
    ]

    effective_max_df = max_df if len(mega_docs) >= 2 else 1.0
    vectorizer = CountVectorizer(max_df=effective_max_df)
    counts = vectorizer.fit_transform(mega_docs).toarray()
    vocab = vectorizer.get_feature_names_out()

    words_per_cluster = counts.sum(axis=1)
    avg_words_per_cluster = words_per_cluster.mean() if len(words_per_cluster) else 0.0
    term_freq_across_clusters = counts.sum(axis=0)
    idf = np.log(1 + avg_words_per_cluster / np.maximum(term_freq_across_clusters, 1))
    ctfidf = counts * idf

    result: dict[int, list[str]] = {}
    for row, cluster_id in enumerate(unique_labels):
        top_indices = ctfidf[row].argsort()[::-1][:top_n]
        result[cluster_id] = [vocab[j] for j in top_indices if ctfidf[row, j] > 0]
    return result


def attach_readme_context(
    clusters: list[TopicCluster],
    class_documents: list[ClassDocument],
    readme_documents: list[ReadmeDocument],
) -> list[TopicCluster]:
    """
    Populates each cluster's readme_paths with every README whose inferred
    package is an ancestor of (or equal to) one of the cluster's member
    packages -- context for a human/LLM reading the cluster, not a signal
    fed into clustering itself.
    """
    package_by_full_name = {c.fullName: c.package for c in class_documents}
    updated: list[TopicCluster] = []
    for cluster in clusters:
        member_packages = {
            package_by_full_name[fn]
            for fn in cluster.member_full_names
            if fn in package_by_full_name
        }
        paths = [
            readme.path
            for readme in readme_documents
            if readme.package
            and any(
                pkg == readme.package or pkg.startswith(readme.package + ".")
                for pkg in member_packages
            )
        ]
        updated.append(dataclasses.replace(cluster, readme_paths=paths))
    return updated


def is_degenerate(clusters: list[TopicCluster], n_classes: int) -> bool:
    """
    True when local HDBSCAN clustering isn't a meaningful grouping for
    this corpus (too few clusters, too many classes in the noise bucket).
    """
    if n_classes < _MIN_CLASSES_FOR_CLUSTERING:
        return True
    non_noise = [c for c in clusters if c.label != -1]
    if not non_noise:
        return True
    noise = next((c for c in clusters if c.label == -1), None)
    noise_fraction = len(noise.member_full_names) / n_classes if noise else 0
    return noise_fraction > _MAX_NOISE_FRACTION


ClusterLabelFn = Callable[[TopicCluster], str]
WholeCorpusGroupingFn = Callable[
    [list[ClassDocument], list[ReadmeDocument]], list[TopicCluster]
]


def discover_topics(
    class_documents: list[ClassDocument],
    readme_documents: list[ReadmeDocument] | None = None,
    *,
    model_name: str = "all-MiniLM-L6-v2",
    min_cluster_size: int = 3,
    max_df: float = 0.85,
    top_n_terms: int = 10,
    label_fn: ClusterLabelFn | None = None,
    whole_corpus_fn: WholeCorpusGroupingFn | None = None,
    force_whole_corpus: bool = False,
) -> list[TopicCluster]:
    """Backward-compatible clusters-only topic-discovery API."""
    return discover_topics_with_centroids(
        class_documents,
        readme_documents,
        model_name=model_name,
        min_cluster_size=min_cluster_size,
        max_df=max_df,
        top_n_terms=top_n_terms,
        label_fn=label_fn,
        whole_corpus_fn=whole_corpus_fn,
        force_whole_corpus=force_whole_corpus,
    ).clusters


def discover_topics_with_centroids(
    class_documents: list[ClassDocument],
    readme_documents: list[ReadmeDocument] | None = None,
    *,
    model_name: str = "all-MiniLM-L6-v2",
    min_cluster_size: int = 3,
    max_df: float = 0.85,
    top_n_terms: int = 10,
    label_fn: ClusterLabelFn | None = None,
    whole_corpus_fn: WholeCorpusGroupingFn | None = None,
    force_whole_corpus: bool = False,
) -> TopicDiscoveryResult:
    """
    Full Mode 1 pipeline: preprocess -> embed -> cluster -> statistically
    label -> attach README context -> (optionally) LLM-label. Returns one
    TopicCluster per HDBSCAN label, including -1 (noise) -- UNLESS the
    result turns out degenerate and whole_corpus_fn was supplied, see below.

    Example usage (in main.py):
        import functools

        client = llm_client.get_client(provider)
        readme_docs = extract_readme_documents(SOURCE_DIR, class_docs)
        class_by_full_name = {c.fullName: c for c in class_docs}

        label_fn = functools.partial(
            topic.label_cluster, client, class_by_full_name=class_by_full_name
        )
        whole_corpus_fn = functools.partial(topic.discover_topics_whole_corpus, client)

        topic_clusters = discover_topics(
            class_docs, readme_docs,
            label_fn=label_fn,
            whole_corpus_fn=whole_corpus_fn,
        )
    """

    readme_documents = readme_documents or []

    docs = [preprocess_document(c.terms) for c in class_documents]
    kept = [(c, d) for c, d in zip(class_documents, docs) if d.strip()]
    if not kept:
        return TopicDiscoveryResult(clusters=[])
    classes_kept, docs_kept = zip(*kept)
    docs_kept = list(docs_kept)

    embeddings = embed_documents(docs_kept, model_name=model_name)

    if force_whole_corpus:
        if whole_corpus_fn is None:
            raise ValueError(
                "force_whole_corpus requires a whole_corpus_fn"
            )
        clusters = whole_corpus_fn(class_documents, readme_documents)
        clusters = attach_readme_context(
            clusters, class_documents, readme_documents
        )
        return TopicDiscoveryResult(
            clusters=clusters,
            centroids=_centroids_from_discovery_embeddings(
                clusters, list(classes_kept), embeddings
            ),
        )

    labels = cluster_documents(embeddings, min_cluster_size=min_cluster_size)
    statistical_terms = label_clusters_statistical(
        docs_kept, labels, max_df=max_df, top_n=top_n_terms
    )

    clusters: list[TopicCluster] = []
    for cluster_id in sorted(int(lbl) for lbl in set(labels)):
        members = [
            c.fullName for c, lbl in zip(classes_kept, labels) if int(lbl) == cluster_id
        ]
        clusters.append(
            TopicCluster(
                label=cluster_id,
                member_full_names=members,
                statistical_terms=statistical_terms.get(cluster_id, []),
            )
        )

    if (
        whole_corpus_fn is not None
        and is_degenerate(clusters, len(class_documents))
    ):
        clusters = whole_corpus_fn(class_documents, readme_documents)
        clusters = attach_readme_context(clusters, class_documents, readme_documents)
        return TopicDiscoveryResult(
            clusters=clusters,
            centroids=_centroids_from_discovery_embeddings(
                clusters, list(classes_kept), embeddings
            ),
        )

    clusters = attach_readme_context(clusters, class_documents, readme_documents)

    if label_fn is not None:
        clusters = [dataclasses.replace(c, llm_label=label_fn(c)) for c in clusters]

    return TopicDiscoveryResult(
        clusters=clusters,
        centroids=_centroids_from_discovery_embeddings(
            clusters, list(classes_kept), embeddings
        ),
    )
