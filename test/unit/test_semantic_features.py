from __future__ import annotations

from domain.cfg_filtering import filter_noise_cfg
from domain.cfg_slicing import slice_from_root
from model import Graph, NodeSemanticFeatures, Phase, UnresolvedGate


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


def test_phase_membership_identity_and_label_round_trip() -> None:
    phase = Phase(
        id="phase-1",
        label="Submit order",
        nodes=["call"],
    )

    assert Phase.from_dict(phase.to_dict()).to_dict() == phase.to_dict()


def test_unresolved_gate_uses_stable_structural_subject_ids() -> None:
    gate = UnresolvedGate(
        id="gate-1",
        left_id="left-call",
        right_id="retained-call",
        confidence=0.4,
        evidence=("missing semantic identity",),
        kind="structure-boundary",
    )

    assert gate.left_id == "left-call"
    assert gate.right_id == "retained-call"


def test_empty_semantic_feature_serializes_without_placeholder_noise() -> None:
    assert NodeSemanticFeatures().to_dict() == {}
