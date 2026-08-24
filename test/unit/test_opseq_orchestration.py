from backend.src.flowmap.domain.opseq_orchestration import has_operation_body
from backend.src.flowmap.model import Graph


def test_entry_exit_shell_has_no_operation_body() -> None:
    graph = Graph.from_dict({
        "nodes": [
            {"id": "entry", "type": "entry", "calleeFullName": "Example.empty"},
            {"id": "exit", "type": "exit"},
        ],
        "edges": [{"from": "entry", "to": "exit", "type": "sequence"}],
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
