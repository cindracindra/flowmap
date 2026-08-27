"""Semantic-feature projection shared by CFG transformation stages."""

from model import NodeSemanticFeatures


def scoped_semantic_features(
    features: dict[str, NodeSemanticFeatures],
    node_ids: set[str],
) -> dict[str, NodeSemanticFeatures]:
    """Keep semantic features belonging to in-scope graph nodes."""
    return {
        node_id: feature
        for node_id, feature in features.items()
        if node_id in node_ids
    }
