"""Public API for FlowMap topic discovery."""

from .clustering import cluster_documents, reduce_embeddings
from .config import (
    DEFAULT_FLOWMAP_CONFIG,
    FLOWMAP_PRESETS,
    FlowMapConfig,
    flowmap_config_for_preset,
)
from .context import attach_readme_context
from .documents import (
    build_class_embedding_document,
    build_class_term_document,
    deduplicate_readme_documents,
    extract_readme_documents,
)
from .embeddings import embed_documents, get_embedding_model
from .labeling import calculate_ctfidf_scores, extract_top_terms_by_cluster
from .orchestration import (
    TopicCoverage,
    TopicDiscoveryResult,
    discover_topics_with_centroids,
    is_degenerate,
    summarize_topic_coverage,
)
from .preprocessing import preprocess_document, split_identifier

__all__ = [
    "DEFAULT_FLOWMAP_CONFIG",
    "FLOWMAP_PRESETS",
    "FlowMapConfig",
    "TopicDiscoveryResult",
    "TopicCoverage",
    "attach_readme_context",
    "build_class_embedding_document",
    "build_class_term_document",
    "calculate_ctfidf_scores",
    "cluster_documents",
    "discover_topics_with_centroids",
    "deduplicate_readme_documents",
    "extract_readme_documents",
    "embed_documents",
    "flowmap_config_for_preset",
    "get_embedding_model",
    "is_degenerate",
    "extract_top_terms_by_cluster",
    "preprocess_document",
    "reduce_embeddings",
    "split_identifier",
    "summarize_topic_coverage",
]
