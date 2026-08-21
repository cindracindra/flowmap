"""Loop scoping and flattened back-edge classification."""

import dataclasses

import networkx as nx

from model import Edge, LoopGroup, Node


def scope_loop_to_instance(loop: LoopGroup, suffix: str) -> LoopGroup:
    return dataclasses.replace(loop, id=f"{loop.id}~{suffix}")


def tag_loop_back_edges(
    nodes: list[Node], edges: list[Edge], root_id: str
) -> list[Edge]:
    """Mark dominance-defined loop back-edges without removing them."""
    nodes_by_id = {node.id: node for node in nodes}
    flow = nx.DiGraph()
    flow.add_nodes_from(nodes_by_id)
    for edge in edges:
        if edge.type == "sequence" or (
            edge.type == "invoke"
            and edge.target in nodes_by_id
            and nodes_by_id[edge.target].type == "entry"
        ):
            flow.add_edge(edge.source, edge.target)

    if root_id not in flow:
        return edges
    dominators = nx.immediate_dominators(flow, root_id)

    def dominates(candidate: str, node_id: str) -> bool:
        current = node_id
        seen: set[str] = set()
        while True:
            if current == candidate:
                return True
            if current in seen:
                return False
            seen.add(current)
            parent = dominators.get(current)
            if parent is None or parent == current:
                return False
            current = parent

    return [
        dataclasses.replace(edge, loopBack=True)
        if edge.type == "sequence" and dominates(edge.target, edge.source)
        else edge
        for edge in edges
    ]
