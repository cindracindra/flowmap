from __future__ import annotations

import sys
from pathlib import Path

FLOWMAP_SRC = Path(__file__).resolve().parents[2] / "backend" / "src" / "flowmap"
sys.path.insert(0, str(FLOWMAP_SRC))

from domain.cfg_pipeline import filter_noise_cfg, flatten_cfg, slice_from_root
from model import Graph, NodeSemanticFeatures, Phase, Transition


def _graph_with_features() -> Graph:
    return Graph.from_dict(
        {
            "entryPoint": "run",
            "nodes": [
                {"id": "entry", "type": "entry", "calleeFullName": "run"},
                {
                    "id": "call",
                    "type": "call",
                    "calleeFullName": "OrderService.submit",
                    "callerMethod": "run",
                },
                {
                    "id": "leaf",
                    "type": "leaf",
                    "calleeFullName": "OrderService.submit",
                },
                {"id": "unused", "type": "entry", "calleeFullName": "unused"},
            ],
            "edges": [
                {"from": "entry", "to": "call", "type": "sequence"},
                {"from": "call", "to": "leaf", "type": "invoke"},
            ],
            "semanticFeatures": {
                "call": {
                    "receiver": "orderService",
                    "receiverType": "app.OrderService",
                    "arguments": ["order"],
                    "argumentTypes": ["app.Order"],
                    "inputIdentifiers": ["order"],
                    "fieldsRead": ["status"],
                    "fieldsWritten": ["submittedAt"],
                    "outputType": "app.Receipt",
                    "domainTypes": ["app.Order", "app.Receipt"],
                    "methodTerms": ["submit"],
                    "observedFeatures": ["receiver", "arguments", "calleeFields"],
                },
                "unused": {"methodTerms": ["unused"]},
            },
        }
    )


def test_semantic_features_round_trip_as_graph_side_car() -> None:
    raw = _graph_with_features().to_dict()

    assert raw["semanticFeatures"]["call"]["receiver"] == "orderService"
    assert raw["semanticFeatures"]["call"]["fieldsWritten"] == ["submittedAt"]
    assert Graph.from_dict(raw).semanticFeatures["call"].outputType == "app.Receipt"


def test_slice_keeps_only_features_for_reached_nodes() -> None:
    sliced = slice_from_root(_graph_with_features(), "entry")

    assert set(sliced.semanticFeatures) == {"call"}


def test_flatten_rekeys_and_copies_features_for_each_clone() -> None:
    graph = _graph_with_features()
    flattened = flatten_cfg(graph)
    call_clone = next(node for node in flattened.nodes if node.origId == "call")

    assert call_clone.id in flattened.semanticFeatures
    assert flattened.semanticFeatures[call_clone.id].arguments == ["order"]
    assert flattened.semanticFeatures[call_clone.id] is not graph.semanticFeatures["call"]


def test_filter_removes_features_for_filtered_call_nodes() -> None:
    graph = Graph.from_dict(
        {
            "entryPoint": "run",
            "nodes": [
                {"id": "entry", "type": "entry", "calleeFullName": "run"},
                {
                    "id": "noise",
                    "type": "call",
                    "calleeFullName": "<operator>.assignment",
                    "callerMethod": "run",
                },
                {
                    "id": "kept",
                    "type": "call",
                    "calleeFullName": "Order.save",
                    "callerMethod": "run",
                },
            ],
            "edges": [
                {"from": "entry", "to": "noise", "type": "sequence"},
                {"from": "noise", "to": "kept", "type": "sequence"},
            ],
            "semanticFeatures": {
                "noise": {"methodTerms": ["assignment"]},
                "kept": {"methodTerms": ["save"]},
            },
        }
    )

    filtered = filter_noise_cfg(graph)

    assert set(filtered.semanticFeatures) == {"kept"}


def test_optional_phase_and_boundary_metadata_round_trip() -> None:
    phase = Phase(
        id="phase-1",
        label="Submit order",
        nodes=["call"],
        structuralAnchors=["branch-1"],
        opened_by=Transition(
            subject="previous",
            reason="gate",
            level=1,
            boundaryType="branch-entry",
            decidedBy="systematic",
            confidence=0.9,
            evidence=["shared-order-input"],
        ),
    )

    assert Phase.from_dict(phase.to_dict()).to_dict() == phase.to_dict()


def test_empty_semantic_feature_serializes_without_placeholder_noise() -> None:
    assert NodeSemanticFeatures().to_dict() == {}
