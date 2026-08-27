from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend" / "src" / "flowmap"))

from model import Edge, Graph, Node, NodeSemanticFeatures  # noqa: E402
from service.cfg import (  # noqa: E402
    attach_targeted_data_edges,
    extract_targeted_ddg_edges,
)


def test_extract_targeted_ddg_edges_embeds_one_batched_request():
    session = MagicMock()
    session.query_script_json.return_value = {
        "edges": [{"from": "source", "to": "target", "type": "data"}],
        "stats": {"candidatePairs": 1, "targetsRequested": 1},
    }

    edges, stats = extract_targeted_ddg_edges(
        session, {"target": ["source"]}
    )

    assert edges == [Edge(source="source", target="target", type="data")]
    assert stats["candidatePairs"] == 1
    script = session.query_script_json.call_args.args[0]
    assert "repeat(_.ddgIn)" not in script
    assert "mutable.Set[Long](target.id)" in script
    assert "while (frontier.nonEmpty" in script
    assert 'sourcesByTarget' in script
    assert '\\"target\\":[\\"source\\"]' in script
    assert "__QUESTIONS_JSON_STRING__" not in script


def test_large_targeted_request_uses_bounded_scala_string_constants():
    session = MagicMock()
    session.query_script_json.return_value = {
        "edges": [],
        "stats": {"missingTargets": []},
    }
    questions = {
        f"c{target}": [f"c{source}" for source in range(80)]
        for target in range(400)
    }

    extract_targeted_ddg_edges(session, questions)

    script = session.query_script_json.call_args.args[0]
    assert "ujson.read(List(" in script
    assert ").mkString)" in script
    # The complete request is larger than the JVM's single-string limit, but
    # no individual generated source line/string fragment approaches it.
    assert len(script) > 65_535
    assert max(map(len, script.splitlines())) < 20_000


def test_attach_targeted_data_edges_keeps_edge_as_the_single_source_of_truth():
    graph = Graph(
        nodes=[Node("source", "call"), Node("target", "call")],
        edges=[Edge("source", "target", "sequence")],
        semanticFeatures={
            "source": NodeSemanticFeatures(methodTerms=["source"]),
            "target": NodeSemanticFeatures(methodTerms=["target"]),
        },
    )

    combined = attach_targeted_data_edges(
        graph,
        [Edge("source", "target", "data")],
        {"target": ["source"]},
    )

    assert [(edge.source, edge.target) for edge in combined.edges if edge.type == "data"] == [
        ("source", "target")
    ]
    assert combined.semanticFeatures == graph.semanticFeatures


def test_attach_targeted_data_edges_rejects_unrequested_relationship():
    graph = Graph(
        nodes=[Node("source", "call"), Node("other", "call"), Node("target", "call")],
        semanticFeatures={
            node_id: NodeSemanticFeatures()
            for node_id in ("source", "other", "target")
        },
    )

    try:
        attach_targeted_data_edges(
            graph,
            [Edge("other", "target", "data")],
            {"target": ["source"]},
        )
    except ValueError as exc:
        assert "unrequested edge" in str(exc)
    else:
        raise AssertionError("unrequested targeted edge was accepted")


def test_attach_targeted_data_edges_rejects_unknown_endpoint():
    graph = Graph(nodes=[Node("target", "call")])
    try:
        attach_targeted_data_edges(
            graph,
            [Edge("missing", "target", "data")],
            {"target": ["missing"]},
        )
    except ValueError as exc:
        assert "unknown endpoint" in str(exc)
    else:
        raise AssertionError("unknown targeted endpoint was accepted")


def test_extract_targeted_ddg_edges_rejects_missing_retained_target():
    session = MagicMock()
    session.query_script_json.return_value = {
        "edges": [],
        "stats": {"missingTargets": ["c404"]},
    }
    try:
        extract_targeted_ddg_edges(session, {"c404": ["c1"]})
    except ValueError as exc:
        assert "c404" in str(exc)
    else:
        raise AssertionError("missing retained target was accepted")
