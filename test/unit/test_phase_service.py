from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

FLOWMAP_SRC = Path(__file__).resolve().parents[2] / "backend" / "src" / "flowmap"
sys.path.insert(0, str(FLOWMAP_SRC))

from domain.phase_resolution import construct_connected_candidates  # noqa: E402
from model import Graph  # noqa: E402
from service.phase import (  # noqa: E402
    label_phase,
    resolve_ambiguous_phase_gates,
)


def _uncertain_subject():
    graph = Graph.from_dict({
        "nodes": [
            {"id": "validate", "type": "call", "calleeFullName": "Order.validate"},
            {"id": "reserve", "type": "call", "calleeFullName": "Stock.reserve"},
        ],
        "edges": [{"from": "validate", "to": "reserve", "type": "sequence"}],
        "semanticFeatures": {
            "validate": {"methodTerms": ["validate", "order"]},
            "reserve": {"methodTerms": ["reserve", "stock"]},
        },
    })
    plan = construct_connected_candidates(graph, ("validate", "reserve"))
    return graph, plan.ambiguousGates[0]


def test_phase_gate_uses_topic_modelling_llm_configuration_and_json_mode() -> None:
    graph, gate = _uncertain_subject()
    client = MagicMock()
    client.complete.return_value = (
        '{"decisions":[{"gate_id":"gate-1","action":"SPLIT",'
        '"confidence":0.91,"reason":"different subprocess"}]}'
    )

    answers = resolve_ambiguous_phase_gates(
        client, graph, (gate,), ("validate",)
    )
    result = answers[("validate", "reserve")]

    assert result == ("SPLIT", 0.91, ("llm:different subprocess",))
    request = client.complete.call_args.kwargs
    assert request["role"] == "small"
    assert request["json_object"] is True
    payload = json.loads(request["user"])
    assert payload["gates"][0]["immediateFrontier"]["callee"] == "Order.validate"
    assert payload["gates"][0]["candidate"]["callee"] == "Stock.reserve"


def test_phase_gate_rejects_invalid_decision() -> None:
    graph, gate = _uncertain_subject()
    client = MagicMock()
    client.complete.return_value = (
        '{"decisions":[{"gate_id":"gate-1","action":"MAYBE"}]}'
    )

    assert resolve_ambiguous_phase_gates(
        client, graph, (gate,), ("validate",)
    ) == {}


def test_phase_gate_batch_rejects_more_than_twenty_ambiguous_gates() -> None:
    graph, gate = _uncertain_subject()
    client = MagicMock()

    try:
        resolve_ambiguous_phase_gates(client, graph, (gate,) * 21, ("validate",))
    except ValueError as error:
        assert "20" in str(error)
    else:
        raise AssertionError("expected the 20-gate batch limit to be enforced")


def test_final_phase_label_uses_topic_llm_pattern_after_membership_is_fixed() -> None:
    graph, _ = _uncertain_subject()
    client = MagicMock()
    client.complete.return_value = ("Stock Reservation")

    label = label_phase(client, graph, ("validate", "reserve"), 0)

    assert label == "Stock Reservation"
    request = client.complete.call_args.kwargs
    assert request["role"] == "small"
    payload = json.loads(request["user"])
    assert [item["id"] for item in payload["operations"]] == [
        "validate", "reserve"
    ]
