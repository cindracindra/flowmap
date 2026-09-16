from __future__ import annotations

import json
import threading
from unittest.mock import MagicMock

from service.method_phase_label import label_method_phases
from service.phase_label_format import valid_phase_label


def _subject(subject_id: str, phase_id: str) -> dict:
    return {
        "id": subject_id,
        "phaseEvidence": [{
            "phaseId": phase_id,
            "method": "Order.checkout:void()",
            "phaseIndex": 2,
            "localPhaseCount": 3,
            "coreSignature": {
                "receivers": ["ledger"],
                "domainTypes": ["ledger"],
                "methodTerms": ["reserve"],
            },
            "operations": [{
                "callee": "Ledger.reserve", "code": "ledger.reserve()",
            }],
        }],
    }


def test_labels_method_phase_subjects_in_one_json_batch() -> None:
    client = MagicMock()
    client.complete.return_value = "\n".join((
        json.dumps({"id": "s1", "label": "Order Validation"}),
        json.dumps({"id": "s2", "label": "Stock & Reservation"}),
    ))
    request = {
        "schemaVersion": "execution-phase-label-v2",
        "subjects": [_subject("s1", "p1"), _subject("s2", "p2")],
    }

    assert label_method_phases(client, request) == {
        "s1": "Order Validation", "s2": "Stock & Reservation",
    }
    call = client.complete.call_args.kwargs
    assert call["json_object"] is False
    assert len(json.loads(call["user"])["subjects"]) == 2
    assert "weak ordering context" in call["system"]


def test_dotted_java_name_counts_as_one_label_word() -> None:
    assert valid_phase_label("unsafe host ping via Runtime.exec")


def test_compact_slashes_are_normalised_before_validation() -> None:
    client = MagicMock()
    client.complete.return_value = json.dumps(
        {"id": "s1", "label": "extract map key/value types"}
    )

    assert label_method_phases(client, {
        "schemaVersion": "execution-phase-label-v2",
        "subjects": [_subject("s1", "p1")],
    }) == {"s1": "extract map key / value types"}


def test_unknown_response_id_reports_expected_batch_ids(capsys) -> None:
    client = MagicMock()
    client.complete.side_effect = [
        json.dumps({"id": "s3", "label": "Wrong Subject"}),
        "\n".join((
            json.dumps({"id": "s1", "label": "Order Validation"}),
            json.dumps({"id": "s2", "label": "Stock Reservation"}),
        )),
    ]

    assert label_method_phases(client, {
        "schemaVersion": "execution-phase-label-v2",
        "subjects": [_subject("s1", "p1"), _subject("s2", "p2")],
    }) == {"s1": "Order Validation", "s2": "Stock Reservation"}
    assert (
        "unknown response id 's3'; ignored; expected one of ['s1', 's2']"
        in capsys.readouterr().err
    )


def test_retries_only_missing_or_invalid_subjects() -> None:
    client = MagicMock()
    client.complete.side_effect = [
        "\n".join((
            json.dumps({"id": "s1", "label": "Order Validation"}),
            json.dumps({"id": "s2", "label": "This label contains far too many unsupported words here"}),
        )),
        json.dumps({"id": "s2", "label": "Stock Reservation"}),
    ]
    request = {
        "schemaVersion": "execution-phase-label-v2",
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
    subject["phaseEvidence"][0]["coreSignature"] = {}

    assert label_method_phases(client, {
        "schemaVersion": "execution-phase-label-v2", "subjects": [subject],
    }) == {}
    client.complete.assert_not_called()


def test_large_request_is_chunked_and_malformed_chunk_is_isolated() -> None:
    client = MagicMock()
    subjects = [_subject(f"s{index}", f"p{index}") for index in range(40)]

    def complete(**kwargs):
        batch = json.loads(kwargs["user"])["subjects"]
        return "\n".join(
            json.dumps({"id": subject["id"], "label": "Phase Work"})
            for subject in batch
        )

    client.complete.side_effect = complete

    result = label_method_phases(client, {
        "schemaVersion": "execution-phase-label-v2",
        "subjects": subjects,
    })

    assert set(result) == {f"s{index}" for index in range(40)}
    assert [
        len(json.loads(call.kwargs["user"])["subjects"])
        for call in client.complete.call_args_list
    ] == [32, 8]


def test_independent_chunks_run_with_bounded_concurrency() -> None:
    barrier = threading.Barrier(4)
    lock = threading.Lock()
    active = 0
    peak_active = 0

    class ConcurrentClient:
        def complete(self, **kwargs) -> str:
            nonlocal active, peak_active
            batch = json.loads(kwargs["user"])
            with lock:
                active += 1
                peak_active = max(peak_active, active)
            try:
                barrier.wait(timeout=2)
                return "\n".join(
                    json.dumps({"id": subject["id"], "label": "Phase Work"})
                    for subject in batch["subjects"]
                )
            finally:
                with lock:
                    active -= 1

    subjects = [_subject(f"s{index}", f"p{index}") for index in range(128)]

    result = label_method_phases(ConcurrentClient(), {
        "schemaVersion": "execution-phase-label-v2",
        "subjects": subjects,
    })

    assert set(result) == {subject["id"] for subject in subjects}
    assert peak_active == 4


def test_malformed_json_line_retries_only_that_subject() -> None:
    client = MagicMock()
    client.complete.side_effect = [
        '{"id":"s1","label":"Order Validation"}\n'
        '{"id":"s2","label":"truncated"',
        '{"id":"s2","label":"Stock Reservation"}',
    ]
    request = {
        "schemaVersion": "execution-phase-label-v2",
        "subjects": [_subject("s1", "p1"), _subject("s2", "p2")],
    }

    assert label_method_phases(client, request) == {
        "s1": "Order Validation", "s2": "Stock Reservation",
    }
    retry = json.loads(client.complete.call_args_list[1].kwargs["user"])
    assert [subject["id"] for subject in retry["subjects"]] == ["s2"]
