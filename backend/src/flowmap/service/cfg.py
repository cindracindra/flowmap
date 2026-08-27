import dataclasses
import json
from pathlib import Path

from joern.joern_session import JoernSession
from model import Edge, Graph

_FULL_CFG_SC = (
    Path(__file__).parent.parent / "joern/scripts/full_cfg.sc"
).read_text()
_DDG_EDGES_SC = (
    Path(__file__).parent.parent / "joern/scripts/ddg_edges.sc"
).read_text()

# JVM class files limit one UTF-8 string constant to 65,535 bytes. Keep each
# embedded request fragment well below that after JSON/Scala escaping.
_SCALA_STRING_CHUNK_CHARS = 12_000


def _relative_source_file(filename: str, source_dir: Path) -> str | None:
    if not filename.strip() or filename.startswith("<"):
        return None
    path = Path(filename)
    if path.is_absolute():
        try:
            path = path.relative_to(source_dir)
        except ValueError:
            pass
    return path.as_posix()


def extract_cfg_structure(
    session: JoernSession, source_dir: str | Path | None = None
) -> Graph:
    """
    Whole-codebase inter-method control-flow structure, excluding DDG edges.
    Returns a Graph with entryPoint=None -- pair with
    classify_roots_and_orphans to populate `roots`/`orphans`.
    """
    graph = Graph.from_dict(session.query_script_json(_FULL_CFG_SC))
    if source_dir is None:
        return graph

    root = Path(source_dir).resolve()
    return dataclasses.replace(
        graph,
        nodes=[
            dataclasses.replace(
                node,
                sourceFile=_relative_source_file(node.sourceFile, root),
            ) if node.sourceFile else node
            for node in graph.nodes
        ],
    )


def extract_targeted_ddg_edges(
    session: JoernSession,
    sources_by_target: dict[str, list[str]],
    *,
    max_depth: int = 10,
) -> tuple[list[Edge], dict[str, object]]:
    """Resolve one batch of retained call-to-call DDG questions."""
    if max_depth <= 0:
        raise ValueError("max_depth must be positive")
    if not sources_by_target:
        return [], {
            "candidatePairs": 0,
            "targetsRequested": 0,
            "targetsQueried": 0,
            "confirmedEdges": 0,
            "visitedNodes": 0,
            "maxVisitedForTarget": 0,
            "missingTargets": [],
        }
    request = {
        "sourcesByTarget": sources_by_target,
        "maxDepth": max_depth,
    }
    request_json = json.dumps(request, separators=(",", ":"))
    request_chunks = [
        request_json[start:start + _SCALA_STRING_CHUNK_CHARS]
        for start in range(0, len(request_json), _SCALA_STRING_CHUNK_CHARS)
    ]
    # Each fragment becomes its own safe string constant. mkString joins them
    # at runtime, preventing the Scala compiler from emitting one oversized
    # JVM UTF-8 constant for a large whole-codebase candidate batch.
    request_expression = "List(\n" + ",\n".join(
        json.dumps(chunk) for chunk in request_chunks
    ) + "\n).mkString"
    script = _DDG_EDGES_SC.replace(
        "__QUESTIONS_JSON_STRING__", request_expression
    )
    payload = session.query_script_json(script)
    if not isinstance(payload, dict):
        raise TypeError("targeted DDG extraction returned a non-object payload")
    raw_edges = payload.get("edges")
    stats = payload.get("stats")
    if not isinstance(raw_edges, list) or not isinstance(stats, dict):
        raise TypeError("targeted DDG extraction omitted edges or stats")
    missing_targets = stats.get("missingTargets", [])
    if not isinstance(missing_targets, list):
        raise TypeError("targeted DDG extraction returned invalid missingTargets")
    if missing_targets:
        raise ValueError(
            "targeted DDG could not resolve retained targets: "
            + ", ".join(map(str, missing_targets))
        )
    return [Edge.from_dict(edge) for edge in raw_edges], stats


def _attach_data_edges(graph: Graph, data_edges: list[Edge]) -> Graph:
    """Attach already-validated data edges to a graph."""
    node_ids = {node.id for node in graph.nodes}
    unknown = [
        edge for edge in data_edges
        if edge.source not in node_ids or edge.target not in node_ids
    ]
    if unknown:
        first = unknown[0]
        raise ValueError(
            "targeted DDG returned an edge with an unknown endpoint "
            f"{first.source!r} -> {first.target!r}"
        )
    return dataclasses.replace(graph, edges=[*graph.edges, *data_edges])


def attach_targeted_data_edges(
    graph: Graph,
    data_edges: list[Edge],
    sources_by_target: dict[str, list[str]],
) -> Graph:
    """Validate a targeted response and attach only submitted relationships."""
    allowed = {
        (source_id, target_id)
        for target_id, source_ids in sources_by_target.items()
        for source_id in source_ids
    }
    invalid = [
        edge
        for edge in data_edges
        if edge.type != "data" or (edge.source, edge.target) not in allowed
    ]
    if invalid:
        first = invalid[0]
        raise ValueError(
            "targeted DDG returned an unrequested edge "
            f"{first.source!r} -> {first.target!r}"
        )
    unique: dict[tuple[str, str], Edge] = {}
    for edge in data_edges:
        unique[(edge.source, edge.target)] = edge
    return _attach_data_edges(graph, list(unique.values()))
