from __future__ import annotations

from collections import defaultdict

from model import Edge, Graph, MethodDefinition, Node


_TRANSPARENT_PHASE_NODE_TYPES = frozenset({"structure", "transfer"})


def _project_call_pairs(
    nodes: dict[str, Node], edges: list[Edge]
) -> list[tuple[str, str]]:
    """Project sequence flow across non-executable structural nodes.

    Calls are endpoints and traversal stops at the first call on each route.
    Method entries/exits and external leaves are boundaries. Structural anchors
    and break/continue transfers carry topology but never become phase work.
    """
    outgoing: dict[str, list[Edge]] = defaultdict(list)
    for edge in edges:
        if edge.type == "sequence":
            outgoing[edge.source].append(edge)

    pairs: list[tuple[str, str]] = []
    seen_pairs: set[tuple[str, str]] = set()
    for source in nodes.values():
        if source.type != "call":
            continue
        pending = list(reversed(outgoing.get(source.id, [])))
        visited_structural: set[str] = set()
        while pending:
            edge = pending.pop()
            target = nodes.get(edge.target)
            if target is None:
                continue
            if target.type == "call":
                if (
                    source.callerMethod == target.callerMethod
                    and (pair := (source.id, target.id)) not in seen_pairs
                ):
                    seen_pairs.add(pair)
                    pairs.append(pair)
                continue
            if target.type not in _TRANSPARENT_PHASE_NODE_TYPES:
                continue
            if target.id in visited_structural:
                continue
            visited_structural.add(target.id)
            pending.extend(reversed(outgoing.get(target.id, [])))
    return pairs


def graph_call_sequence_pairs(graph: Graph) -> list[tuple[str, str]]:
    return _project_call_pairs(
        {node.id: node for node in graph.nodes}, graph.edges
    )


def method_call_sequence_pairs(
    method: MethodDefinition,
) -> list[tuple[str, str]]:
    return _project_call_pairs(
        {
            method.entry.id: method.entry,
            **{node.id: node for node in method.nodes},
        },
        method.sequenceEdges,
    )
