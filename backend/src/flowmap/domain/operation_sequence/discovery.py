from __future__ import annotations

from domain.cfg_slicing import slice_from_root
from model import Graph


def has_operation_body(graph: Graph) -> bool:
    """Return whether a filtered operation sequence has executable content."""
    return any(node.type == "call" for node in graph.nodes)


def discover_operation_sequences(filtered_graph: Graph) -> dict[str, Graph]:
    """Slice one executable operation sequence from every classified root."""
    operations: dict[str, Graph] = {}
    for root_id in filtered_graph.roots:
        operation = slice_from_root(filtered_graph, root_id)
        if not has_operation_body(operation):
            raise ValueError(
                f"classified root {root_id!r} has no executable operation body"
            )
        operations[root_id] = operation
    return operations
