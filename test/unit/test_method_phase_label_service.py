from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

FLOWMAP_SRC = Path(__file__).resolve().parents[2] / "backend" / "src" / "flowmap"
sys.path.insert(0, str(FLOWMAP_SRC))

from service.method_phase_label import label_method_phases  # noqa: E402
from service.phase_label_format import valid_phase_label  # noqa: E402


def _subject(subject_id: str, phase_id: str) -> dict:
    return {
        "id": subject_id,
        "phaseIds": [phase_id],
        "phaseEvidence": [{
            "phaseId": phase_id,
            "method": {"entryId": "entry", "fullName": "Order.checkout:void()"},
            "phaseIndex": 2,
            "localPhaseCount": 3,
            "operations": [{
                "callNodeId": "reserve", "callee": "Ledger.reserve", "code": "ledger.reserve()",
                "receiver": "ledger", "arguments": [], "inputs": [], "fieldsRead": [],
                "fieldsWritten": [], "domainTypes": ["Ledger"], "methodTerms": ["reserve"],
            }],
        }],
    }


def test_labels_method_phase_subjects_in_one_json_batch() -> None:
    client = MagicMock()
    client.complete.return_value = json.dumps({"labels": [
        {"id": "s1", "label": "Order Validation"},
        {"id": "s2", "label": "Stock & Reservation"},
    ]})
    request = {
        "schemaVersion": "method-phase-label-v1",
        "subjects": [_subject("s1", "p1"), _subject("s2", "p2")],
    }

    assert label_method_phases(client, request) == {
        "s1": "Order Validation", "s2": "Stock & Reservation",
    }
    call = client.complete.call_args.kwargs
    assert call["json_object"] is True
    assert len(json.loads(call["user"])["subjects"]) == 2
    assert "weak ordering context" in call["system"]


def test_dotted_java_name_counts_as_one_label_word() -> None:
    assert valid_phase_label("unsafe host ping via Runtime.exec")


def test_retries_only_missing_or_invalid_subjects() -> None:
    client = MagicMock()
    client.complete.side_effect = [
        json.dumps({"labels": [
            {"id": "s1", "label": "Order Validation"},
            {"id": "s2", "label": "This label contains far too many unsupported words here"},
        ]}),
        json.dumps({"labels": [{"id": "s2", "label": "Stock Reservation"}]}),
    ]
    request = {
        "schemaVersion": "method-phase-label-v1",
        "subjects": [_subject("s1", "p1"), _subject("s2", "p2")],
    }

    assert label_method_phases(client, request) == {
        "s1": "Order Validation", "s2": "Stock Reservation",
    }
    retry = json.loads(client.complete.call_args_list[1].kwargs["user"])
    assert [subject["id"] for subject in retry["subjects"]] == ["s2"]


def test_preflight_skips_subject_without_semantic_evidence() -> None:
    client = MagicMock()
    subject = _subject("s1", "p1")
    subject["phaseEvidence"][0]["operations"] = []

    assert label_method_phases(client, {
        "schemaVersion": "method-phase-label-v1", "subjects": [subject],
    }) == {}
    client.complete.assert_not_called()


def test_large_request_is_chunked_and_malformed_chunk_is_isolated() -> None:
    client = MagicMock()
    subjects = [_subject(f"s{index}", f"p{index}") for index in range(10)]
    client.complete.side_effect = [
        "{\"labels\":[{\"id\":\"s0\",\"label\":\"truncated",
        json.dumps({"labels": [
            {"id": f"s{index}", "label": f"Phase Work {index}"}
            for index in range(8, 10)
        ]}),
        json.dumps({"labels": [
            {"id": f"s{index}", "label": f"Recovered Work {index}"}
            for index in range(4)
        ]}),
        json.dumps({"labels": [
            {"id": f"s{index}", "label": f"Recovered Work {index}"}
            for index in range(4, 8)
        ]}),
    ]

    result = label_method_phases(client, {
        "schemaVersion": "method-phase-label-v1",
        "subjects": subjects,
    })

    assert set(result) == {f"s{index}" for index in range(10)}
    assert [
        len(json.loads(call.kwargs["user"])["subjects"])
        for call in client.complete.call_args_list
    ] == [8, 2, 4, 4]
