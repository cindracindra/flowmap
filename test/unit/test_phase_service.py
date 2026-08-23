from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

FLOWMAP_SRC = Path(__file__).resolve().parents[2] / "backend" / "src" / "flowmap"
sys.path.insert(0, str(FLOWMAP_SRC))

from domain.phase_resolution import collect_uncertain_gates  # noqa: E402
from domain.phase_segmentation import analyse  # noqa: E402
from llm.client import LLMError  # noqa: E402
from model import Graph  # noqa: E402
from service.phase import (  # noqa: E402
    label_phase,
    resolve_phase_gate_batch,
)


def _uncertain_subject():
    graph = Graph.from_dict({
        "nodes": [
            {"id": "entry", "type": "entry", "calleeFullName": "run"},
            {
                "id": "validate", "type": "call",
                "callerMethod": "run", "calleeFullName": "Order.validate",
            },
            {
                "id": "reserve", "type": "call",
                "callerMethod": "run", "calleeFullName": "Stock.reserve",
            },
        ],
        "edges": [
            {"from": "entry", "to": "validate", "type": "sequence"},
            {"from": "validate", "to": "reserve", "type": "sequence"},
        ],
        "semanticFeatures": {
            "validate": {"methodTerms": ["validate", "order"]},
            "reserve": {"methodTerms": ["reserve", "stock"]},
        },
    })
    question = collect_uncertain_gates(analyse(graph))[0]
    return graph, question


def test_phase_gate_uses_topic_modelling_llm_configuration_and_json_mode() -> None:
    graph, question = _uncertain_subject()
    client = MagicMock()
    client.complete.return_value = (
        '{"decisions":[{"id":"q-1","action":"SPLIT",'
        '"confidence":0.91,"reason":"different subprocess"}]}'
    )

    answers = resolve_phase_gate_batch(client, graph, (question,))
    result = answers["q-1"]

    assert result == ("SPLIT", 0.91, ("llm:different subprocess",))
    request = client.complete.call_args.kwargs
    assert request["role"] == "small"
    assert request["json_object"] is True
    payload = json.loads(request["user"])
    assert payload["operations"]["validate"]["callee"] == "Order.validate"
    assert payload["operations"]["reserve"]["callee"] == "Stock.reserve"
    assert payload["questions"][0]["currentPhase"] == ["validate"]
    assert payload["questions"][0]["frontier"] == "validate"
    assert payload["questions"][0]["candidate"] == "reserve"


def test_phase_gate_rejects_invalid_decision() -> None:
    graph, question = _uncertain_subject()
    client = MagicMock()
    client.complete.return_value = (
        '{"decisions":[{"id":"q-1","action":"MAYBE"}]}'
    )

    assert resolve_phase_gate_batch(client, graph, (question,)) == {}


def test_empty_phase_gate_batch_does_not_call_llm() -> None:
    graph, _ = _uncertain_subject()
    client = MagicMock()

    assert resolve_phase_gate_batch(client, graph, ()) == {}
    client.complete.assert_not_called()


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


def test_final_phase_label_reads_evidence_from_flattened_clone_ids() -> None:
    graph = Graph.from_dict({
        "nodes": [{
            "id": "reserve~7", "origId": "reserve", "type": "call",
            "calleeFullName": "Stock.reserve", "code": "stock.reserve(order)",
        }],
        "edges": [],
        "semanticFeatures": {
            "reserve~7": {"methodTerms": ["reserve", "stock"]},
        },
    })
    client = MagicMock()
    client.complete.return_value = "Stock Reservation"

    assert label_phase(client, graph, ("reserve~7",), 0) == "Stock Reservation"

    payload = json.loads(client.complete.call_args.kwargs["user"])
    assert payload["operations"][0]["id"] == "reserve~7"
    assert payload["operations"][0]["callee"] == "Stock.reserve"
    assert payload["operations"][0]["methodTerms"] == ["reserve", "stock"]


def test_final_phase_label_accepts_up_to_eight_words() -> None:
    graph, _ = _uncertain_subject()
    client = MagicMock()
    client.complete.return_value = "Update Cart Item Quantity And Recalculate Total Value"

    assert label_phase(client, graph, ("reserve",), 0) == (
        "Update Cart Item Quantity And Recalculate Total Value"
    )


def test_final_phase_label_rejects_and_reports_wrong_format(capsys) -> None:
    graph, _ = _uncertain_subject()
    graph.entryPoint = "Cart.updateQuantities"
    client = MagicMock()
    client.complete.return_value = "The phase updates stock.\nExtra explanation."

    assert label_phase(client, graph, ("reserve",), 2) is None

    error = capsys.readouterr().err
    assert "[phase-label] Cart.updateQuantities phase-3" in error
    assert "invalid format" in error
    assert "Extra explanation" in error


def test_final_phase_label_reports_provider_error(capsys) -> None:
    graph, _ = _uncertain_subject()
    client = MagicMock()
    client.complete.side_effect = LLMError("rate limited")

    assert label_phase(client, graph, ("reserve",), 0) is None

    error = capsys.readouterr().err
    assert "phase-1: provider error: rate limited" in error
