from __future__ import annotations

import dataclasses

import numpy as np

from model import ClassDocument, ReadmeDocument, TopicCluster


def attach_readme_context(
    clusters: list[TopicCluster],
    class_documents: list[ClassDocument],
    readme_documents: list[ReadmeDocument],
) -> list[TopicCluster]:
    """Attach markdown belonging to a cluster member's package ancestry."""
    package_by_full_name = {
        document.fullName: document.package for document in class_documents
    }
    updated: list[TopicCluster] = []
    for cluster in clusters:
        member_packages = {
            package_by_full_name[full_name]
            for full_name in cluster.member_full_names
            if full_name in package_by_full_name
        }
        paths = [
            readme.path
            for readme in readme_documents
            if readme.package
            and any(
                package == readme.package
                or package.startswith(readme.package + ".")
                for package in member_packages
            )
        ]
        updated.append(dataclasses.replace(cluster, readme_paths=paths))
    return updated


def centroids_from_embeddings(
    clusters: list[TopicCluster],
    classes: list[ClassDocument],
    embeddings: np.ndarray,
) -> dict[int, np.ndarray]:
    """Calculate normalized centroids from the original discovery embeddings."""
    embedding_by_name = {
        document.fullName: embedding
        for document, embedding in zip(classes, embeddings)
    }
    centroids: dict[int, np.ndarray] = {}
    for cluster in clusters:
        if cluster.label == -1:
            continue
        members = [
            embedding_by_name[full_name]
            for full_name in cluster.member_full_names
            if full_name in embedding_by_name
        ]
        if not members:
            continue
        centroid = np.mean(members, axis=0)
        norm = np.linalg.norm(centroid)
        if norm > 0:
            centroids[cluster.label] = centroid / norm
    return centroids
