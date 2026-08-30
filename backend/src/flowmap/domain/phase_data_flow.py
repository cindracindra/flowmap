"""Build targeted DDG questions from the filtered method-level CFG."""

from __future__ import annotations

from domain.phase_topology import graph_call_sequence_pairs
from model import Graph


def build_phase_data_flow_questions(graph: Graph) -> dict[str, list[str]]:
    """Return candidate data sources grouped by their sequence target.

    Phase relationship evaluation only compares ordinary, method-local
    call-to-call sequence transitions. Build the Joern batch in that exact
    shape, avoiding pair objects and all raw/noisy calls already removed from
    ``graph``. Keys and source lists are sorted to keep requests and evaluation
    artifacts deterministic.
    """
    sources_by_target: dict[str, set[str]] = {}
    for source, target in graph_call_sequence_pairs(graph):
        sources_by_target.setdefault(target, set()).add(source)

    return {
        target_id: sorted(sources_by_target[target_id])
        for target_id in sorted(sources_by_target)
    }
