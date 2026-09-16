from unittest.mock import MagicMock, patch

import numpy as np

from backend.src.flowmap.domain.operation_sequence import (
    assign_operation_sequences_to_topics,
    discover_operation_sequences,
    has_operation_body,
    label_operation_sequences,
)
from backend.src.flowmap.model import (
    Graph,
    MethodDocument,
    Node,
    TopicAssignment,
    TopicCluster,
)


def test_entry_exit_shell_has_no_operation_body() -> None:
    graph = Graph.from_dict({
        "nodes": [
            {"id": "entry", "type": "entry", "calleeFullName": "Example.empty"},
            {"id": "exit", "type": "exit"},
        ],
        "edges": [{"from": "entry", "to": "exit", "type": "sequence"}],
    })

    assert not has_operation_body(graph)


def test_structure_and_transfer_shell_has_no_operation_body() -> None:
    graph = Graph.from_dict({
        "nodes": [
            {"id": "entry", "type": "entry", "calleeFullName": "Example.empty"},
            {"id": "anchor", "type": "structure"},
            {"id": "break", "type": "transfer", "transferKind": "break", "targetStructureGroupId": "loop"},
        ],
        "edges": [
            {"from": "entry", "to": "anchor", "type": "sequence"},
            {"from": "anchor", "to": "break", "type": "sequence"},
        ],
    })

    assert not has_operation_body(graph)


def test_call_node_is_an_operation_body() -> None:
    graph = Graph.from_dict({
        "nodes": [
            {"id": "entry", "type": "entry", "calleeFullName": "Example.work"},
            {"id": "call", "type": "call", "calleeFullName": "Work.run"},
        ],
        "edges": [{"from": "entry", "to": "call", "type": "sequence"}],
    })

    assert has_operation_body(graph)


@patch(
    "backend.src.flowmap.domain.operation_sequence.discovery.slice_from_root"
)
def test_discover_operation_sequences_slices_every_root(mock_slice) -> None:
    mock_slice.side_effect = [
        Graph(nodes=[Node(id="call-a", type="call")]),
        Graph(nodes=[Node(id="call-b", type="call")]),
    ]
    graph = Graph(roots=["root-a", "root-b"])

    result = discover_operation_sequences(graph)

    assert list(result) == ["root-a", "root-b"]
    assert [call.args[1] for call in mock_slice.call_args_list] == [
        "root-a",
        "root-b",
    ]


@patch(
    "backend.src.flowmap.domain.operation_sequence.orchestration."
    "assign_operation_topics"
)
def test_statistical_topic_assignment_uses_centroids_even_after_llm_labelling(
    mock_assign,
) -> None:
    cluster = TopicCluster(
        label=1,
        statistical_terms=["order"],
        llm_label="Order Management",
        fallback=False,
    )
    mock_assign.return_value = {}

    assign_operation_sequences_to_topics(
        {}, [cluster], [], {1: np.array([1.0])}
    )

    assert mock_assign.call_args.kwargs["formed_by_llm"] is False


@patch(
    "backend.src.flowmap.domain.operation_sequence.orchestration."
    "assign_operation_topics"
)
def test_whole_corpus_topic_assignment_uses_llm_provenance(mock_assign) -> None:
    cluster = TopicCluster(
        label=1,
        llm_label="Order Management",
        fallback=True,
    )
    classifier = MagicMock()
    mock_assign.return_value = {}

    assign_operation_sequences_to_topics(
        {},
        [cluster],
        [],
        {1: np.array([1.0])},
        classify_fn=classifier,
    )

    assert mock_assign.call_args.kwargs["formed_by_llm"] is True
    assert mock_assign.call_args.kwargs["classify_fn"] is classifier


def test_label_orchestration_supplies_each_assigned_topic() -> None:
    operation = Graph(entryPoint="Order.checkout")
    cluster = TopicCluster(label=7, llm_label="Orders")
    labeler = MagicMock(return_value={"op": "Order Checkout"})
    result = label_operation_sequences(
        {"op": operation},
        {"op": [TopicAssignment(label=7, similarity=0.9)]},
        [cluster],
        label_fn=labeler,
    )

    assert result == {"op": "Order Checkout"}
    assert labeler.call_args.args == ({"op": (operation, cluster)},)
