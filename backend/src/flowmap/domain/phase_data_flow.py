"""Build targeted DDG questions from the filtered method-level CFG."""

from __future__ import annotations

from model import Graph


def build_phase_data_flow_questions(graph: Graph) -> dict[str, list[str]]:
    """Return candidate data sources grouped by their sequence target.

    Phase relationship evaluation only compares ordinary, method-local
    call-to-call sequence transitions. Build the Joern batch in that exact
    shape, avoiding pair objects and all raw/noisy calls already removed from
    ``graph``. Keys and source lists are sorted to keep requests and evaluation
    artifacts deterministic.
    """
    nodes_by_id = {node.id: node for node in graph.nodes}
    sources_by_target: dict[str, set[str]] = {}

    for edge in graph.edges:
        if (
            edge.type != "sequence"
            or edge.returnFrom is not None
            or edge.loopBack
        ):
            continue

        source = nodes_by_id.get(edge.source)
        target = nodes_by_id.get(edge.target)
        if source is None or target is None:
            continue
        if source.type != "call" or target.type != "call":
            continue

        sources_by_target.setdefault(target.id, set()).add(source.id)

    return {
        target_id: sorted(sources_by_target[target_id])
        for target_id in sorted(sources_by_target)
    }
