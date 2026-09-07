from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from sklearn.cluster import HDBSCAN

from model import ClassDocument

from .config import DEFAULT_FLOWMAP_CONFIG, FlowMapConfig
from .documents import prepare_class_documents
from .embeddings import embed_documents


@dataclass(frozen=True, slots=True)
class EmbeddedCorpus:
    classes: list[ClassDocument]
    embedding_texts: list[str]
    term_texts: list[str]
    embeddings: np.ndarray


def embed_class_documents(
    class_documents: list[ClassDocument],
) -> EmbeddedCorpus:
    """Prepare and embed class evidence while keeping all rows aligned."""
    classes, embedding_texts, term_texts = prepare_class_documents(
        class_documents
    )
    embeddings = (
        embed_documents(
            embedding_texts, model_name=DEFAULT_FLOWMAP_CONFIG["model_name"]
        )
        if embedding_texts
        else np.empty((0, 0))
    )
    return EmbeddedCorpus(
        classes=classes,
        embedding_texts=embedding_texts,
        term_texts=term_texts,
        embeddings=embeddings,
    )


def reduce_embeddings(
    embeddings: np.ndarray,
    *,
    n_components: int,
    n_neighbors: int,
    random_state: int = DEFAULT_FLOWMAP_CONFIG["umap_random_state"],
) -> np.ndarray:
    """Apply deterministic cosine UMAP using the evaluation methodology."""
    if n_neighbors >= len(embeddings):
        raise ValueError(
            f"UMAP n_neighbors={n_neighbors} must be smaller than the "
            f"{len(embeddings)} corpus documents"
        )

    numba_cache_dir = Path(tempfile.gettempdir()) / "flowmap-numba-cache"
    numba_cache_dir.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("NUMBA_CACHE_DIR", str(numba_cache_dir))
    from umap import UMAP

    return UMAP(
        n_components=n_components,
        n_neighbors=n_neighbors,
        min_dist=DEFAULT_FLOWMAP_CONFIG["umap_min_dist"],
        metric="cosine",
        random_state=random_state,
    ).fit_transform(embeddings)


def cluster_documents(
    embeddings: np.ndarray,
    min_cluster_size: int = 3,
    min_samples: int = 3,
) -> np.ndarray:
    """Assign HDBSCAN labels, including -1 for documents considered noise."""
    if len(embeddings) < 2:
        return np.full(len(embeddings), -1, dtype=int)
    return HDBSCAN(
        min_cluster_size=min_cluster_size,
        min_samples=min_samples,
        metric="euclidean",
        copy=False,
    ).fit_predict(embeddings)


def cluster_embedded_corpus(
    corpus: EmbeddedCorpus,
    config: FlowMapConfig,
    *,
    random_state: int = DEFAULT_FLOWMAP_CONFIG["umap_random_state"],
) -> np.ndarray:
    """Run the complete UMAP-to-HDBSCAN clustering process."""
    if len(corpus.embeddings) < config.min_cluster_size:
        return np.full(len(corpus.embeddings), -1, dtype=int)
    reduced = reduce_embeddings(
        corpus.embeddings,
        n_components=config.umap_n_components,
        n_neighbors=config.umap_n_neighbors,
        random_state=random_state,
    )
    return cluster_documents(
        reduced,
        min_cluster_size=config.min_cluster_size,
        min_samples=config.min_samples,
    )
