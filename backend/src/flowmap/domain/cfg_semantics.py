"""Semantic-feature projection shared by CFG transformation stages."""

import dataclasses

from model import Edge, NodeSemanticFeatures


def scoped_semantic_features(
    features: dict[str, NodeSemanticFeatures],
    node_ids: set[str],
    edges: list[Edge],
) -> dict[str, NodeSemanticFeatures]:
    """Keep in-scope features and rebuild their data-flow node IDs."""
    sources: dict[str, list[str]] = {}
    consumers: dict[str, list[str]] = {}
    for edge in edges:
        if edge.type != "data":
            continue
        sources.setdefault(edge.target, []).append(edge.source)
        consumers.setdefault(edge.source, []).append(edge.target)

    return {
        node_id: dataclasses.replace(
            feature,
            dataSourceIds=list(dict.fromkeys(sources.get(node_id, ()))),
            dataConsumerIds=list(dict.fromkeys(consumers.get(node_id, ()))),
        )
        for node_id, feature in features.items()
        if node_id in node_ids
    }
