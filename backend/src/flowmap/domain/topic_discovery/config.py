from __future__ import annotations

from dataclasses import dataclass


DEFAULT_FLOWMAP_CONFIG = {
    "model_name": "all-MiniLM-L6-v2",
    "umap_enabled": True,
    "umap_min_dist": 0.0,
    "umap_random_state": 11,
}


@dataclass(frozen=True, slots=True)
class FlowMapConfig:
    """The four supported topic-clustering tuning parameters."""

    umap_n_components: int = 5
    umap_n_neighbors: int = 5
    min_cluster_size: int = 5
    min_samples: int = 3

    def __post_init__(self) -> None:
        if self.umap_n_components < 2:
            raise ValueError("umap_n_components must be at least 2")
        if self.umap_n_neighbors < 2:
            raise ValueError("umap_n_neighbors must be at least 2")
        if self.min_cluster_size < 2:
            raise ValueError("min_cluster_size must be at least 2")
        if self.min_samples < 1:
            raise ValueError("min_samples must be positive")


FLOWMAP_PRESETS: dict[str, dict[str, int]] = {
    "fine": {
        "umap_n_components": 5,
        "umap_n_neighbors": 5,
        "min_cluster_size": 3,
        "min_samples": 1,
    },
    "balanced": {
        "umap_n_components": 5,
        "umap_n_neighbors": 5,
        "min_cluster_size": 5,
        "min_samples": 3,
    },
    "coarse": {
        "umap_n_components": 10,
        "umap_n_neighbors": 10,
        "min_cluster_size": 8,
        "min_samples": 5,
    },
}


def flowmap_config_for_preset(preset: str) -> FlowMapConfig:
    try:
        return FlowMapConfig(**FLOWMAP_PRESETS[preset])
    except KeyError as exc:
        raise ValueError(f"unknown FlowMap preset: {preset!r}") from exc
