from __future__ import annotations

from functools import lru_cache

import numpy as np
from sentence_transformers import SentenceTransformer


@lru_cache(maxsize=None)
def get_embedding_model(model_name: str) -> SentenceTransformer:
    """Load each sentence-transformer model once per process."""
    return SentenceTransformer(model_name)


def embed_documents(
    texts: list[str], model_name: str = "all-MiniLM-L6-v2"
) -> np.ndarray:
    """Encode and normalize a batch of topic-model documents."""
    model = get_embedding_model(model_name)
    embeddings = model.encode(texts, normalize_embeddings=True)
    return np.asarray(embeddings)
