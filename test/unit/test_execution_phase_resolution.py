from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock

FLOWMAP_SRC = Path(__file__).resolve().parents[2] / "backend" / "src" / "flowmap"
sys.path.insert(0, str(FLOWMAP_SRC))

from domain.execution_phase.method_analysis import MethodAnalysis  # noqa: E402
from domain.execution_phase.resolution import (  # noqa: E402
    build_gate_question,
    build_gate_questions,
    resolve_uncertain_gates,
)
from model import (  # noqa: E402
    MethodDefinition,
    Node,
    NodeSemanticFeatures,
    Phase,
    UnresolvedGate,
)
from service.phase import resolve_execution_phase_gate_batch  # noqa: E402
from execution_phase_test_support import method_analysis_snapshot  # noqa: E402


def _expected_prompt_payload(*, direct_flow: bool = True) -> dict:
    missing = [
        "arguments",
        "domain_types",
        "fields_read",
        "fields_written",
        "method_terms",
        "output_types",
    ]
    return {
        "gateId": "validate-reserve",
        "method": "Checkout.submit",
        "boundaryKind": "operation-boundary",
        "systematicAssessment": {
            "reasonCode": "INSUFFICIENT_EVIDENCE",
            "confidence": 0.4,
            "positiveEvidence": [],
            "contradictoryEvidence": [],
            "missingObservations": {"left": missing, "right": missing},
            "directFlowAcrossBoundary": direct_flow,
        },
        "leftGroup": {
            "coreSignature": {"receivers": ["validator"], "inputs": ["order"]},
            "operations": [{
                "callee": "Validator.validate",
                "code": "validate(order)",
            }],
        },
        "rightGroup": {
            "coreSignature": {"receivers": ["inventory"], "inputs": ["order"]},
            "operations": [{
                "callee": "Inventory.reserve",
                "code": "reserve(order)",
            }],
        },
    }


def _method() -> MethodDefinition:
    return MethodDefinition(
        entryId="entry",
        methodFullName="Checkout.submit",
        entry=Node("entry", "entry", calleeFullName="Checkout.submit"),
        nodes=[
            Node("validate", "call", calleeFullName="Validator.validate", code="validate(order)"),
            Node("reserve", "call", calleeFullName="Inventory.reserve", code="reserve(order)"),
        ],
        semanticFeatures={
            "validate": NodeSemanticFeatures(
                receiver="validator",
                inputIdentifiers=["order"],
                observedFeatures=["receiver", "inputs"],
            ),
            "reserve": NodeSemanticFeatures(
                receiver="inventory",
                inputIdentifiers=["order"],
                observedFeatures=["receiver", "inputs"],
            ),
        },
    )


def test_build_gate_question_uses_current_phase_context() -> None:
    method = _method()
    gate = UnresolvedGate("validate-reserve", "validate", "reserve", 0.4)
    analysis = MethodAnalysis(
        "entry",
        (Phase(nodes=["validate"]), Phase(nodes=["reserve"])),
        unresolved_gates=(gate,),
    )

    question = build_gate_question(
        gate,
        method,
        analysis,
        frozenset({("validate", "reserve")}),
    )

    assert question is not None
    assert question.to_prompt_payload() == _expected_prompt_payload()


def test_gate_direct_flow_uses_only_the_recorded_frontier_pair() -> None:
    method = _method()
    gate = UnresolvedGate("middle-reserve", "middle", "reserve", 0.4)
    analysis = MethodAnalysis(
        "entry",
        (Phase(nodes=["validate", "middle"]), Phase(nodes=["reserve"])),
        unresolved_gates=(gate,),
    )

    question = build_gate_question(
        gate,
        method,
        analysis,
        frozenset({("validate", "reserve")}),
    )

    assert question is not None
    assert question.direct_flow_across_boundary is False


def test_build_gate_question_skips_gate_already_removed_by_merge() -> None:
    method = _method()
    gate = UnresolvedGate("validate-reserve", "validate", "reserve", 0.4)
    analysis = MethodAnalysis("entry", (Phase(nodes=["validate", "reserve"]),))

    assert build_gate_question(gate, method, analysis, frozenset()) is None


def test_build_gate_questions_collects_current_gates_across_methods() -> None:
    method = _method()
    gate = UnresolvedGate("validate-reserve", "validate", "reserve", 0.4)
    analysis = MethodAnalysis(
        "entry",
        (Phase(nodes=["validate"]), Phase(nodes=["reserve"])),
        unresolved_gates=(gate,),
    )

    questions = build_gate_questions(
        {"entry": method},
        {"entry": analysis},
        frozenset(),
    )

    assert [question.to_prompt_payload() for question in questions] == [
        _expected_prompt_payload(direct_flow=False)
    ]


def test_resolve_uncertain_gates_merges_and_removes_answered_gate() -> None:
    method = _method()
    gate = UnresolvedGate("validate-reserve", "validate", "reserve", 0.4)
    analyses = {
        "entry": MethodAnalysis(
            "entry",
            (Phase(nodes=["validate"]), Phase(nodes=["reserve"])),
            unresolved_gates=(gate,),
        )
    }

    resolved = resolve_uncertain_gates(
        {"entry": method},
        analyses,
        frozenset(),
        lambda questions: {
            questions[0].id: ("MERGE", 0.9, ("llm:same responsibility",))
        },
    )

    assert resolved == 1
    assert method_analysis_snapshot(analyses["entry"]) == method_analysis_snapshot(
        MethodAnalysis("entry", (Phase(nodes=["validate", "reserve"]),))
    )


def test_resolve_uncertain_gates_split_removes_gate_without_merging() -> None:
    method = _method()
    gate = UnresolvedGate("validate-reserve", "validate", "reserve", 0.4)
    analyses = {
        "entry": MethodAnalysis(
            "entry",
            (Phase(nodes=["validate"]), Phase(nodes=["reserve"])),
            unresolved_gates=(gate,),
        )
    }

    resolve_uncertain_gates(
        {"entry": method},
        analyses,
        frozenset(),
        lambda questions: {
            questions[0].id: ("SPLIT", 0.9, ("llm:separate responsibilities",))
        },
    )

    assert method_analysis_snapshot(analyses["entry"]) == method_analysis_snapshot(
        MethodAnalysis(
            "entry",
            (Phase(nodes=["validate"]), Phase(nodes=["reserve"])),
        )
    )


def test_phase_service_resolves_compact_gate_questions() -> None:
    method = _method()
    gate = UnresolvedGate("validate-reserve", "validate", "reserve", 0.4)
    analysis = MethodAnalysis(
        "entry",
        (Phase(nodes=["validate"]), Phase(nodes=["reserve"])),
        unresolved_gates=(gate,),
    )
    question = build_gate_question(gate, method, analysis, frozenset())
    assert question is not None
    client = MagicMock()
    client.complete.return_value = json.dumps({
        "id": question.id,
        "action": "MERGE",
        "confidence": 0.8,
    })

    answers = resolve_execution_phase_gate_batch(client, (question,))

    assert answers == {
        question.id: ("MERGE", 0.8, ("llm-semantic-decision",))
    }
    payload = json.loads(client.complete.call_args.kwargs["user"])
    system_prompt = client.complete.call_args.kwargs["system"]
    expected_question = _expected_prompt_payload(direct_flow=False)
    expected_question = {"id": question.id, **expected_question}
    assert payload == {
        "questions": [expected_question]
    }
    assert "leftGroup and rightGroup" in system_prompt
    assert "candidate" not in system_prompt
    assert (
        '{"id":"q-1","action":"MERGE","confidence":0.85}'
        in system_prompt
    )


def test_phase_service_reports_split_and_invalid_results(capsys) -> None:
    method = _method()
    gate = UnresolvedGate("validate-reserve", "validate", "reserve", 0.4)
    analysis = MethodAnalysis(
        "entry",
        (Phase(nodes=["validate"]), Phase(nodes=["reserve"])),
        unresolved_gates=(gate,),
    )
    question = build_gate_question(gate, method, analysis, frozenset())
    assert question is not None
    invalid_question = replace(question, id="question:invalid")
    client = MagicMock()
    client.complete.return_value = "\n".join((
        json.dumps({"id": question.id, "action": "SPLIT", "confidence": 0.9}),
        json.dumps({
            "id": invalid_question.id, "action": "MAYBE", "confidence": 0.5,
        }),
    ))

    answers = resolve_execution_phase_gate_batch(
        client, (question, invalid_question)
    )

    assert answers == {
        question.id: ("SPLIT", 0.9, ("llm-semantic-decision",))
    }
    diagnostics = capsys.readouterr().err
    assert f"result id={question.id} action=SPLIT" in diagnostics
    assert f"result id={invalid_question.id} status=INVALID_ACTION" in diagnostics
    assert (
        f"result id={invalid_question.id} status=UNRESOLVED_MISSING_OR_INVALID"
        in diagnostics
    )
    assert "batch summary requested=2 merge=0 split=1 unresolved=1" in diagnostics


def test_phase_service_reports_invalid_json_after_retries(capsys) -> None:
    method = _method()
    gate = UnresolvedGate("validate-reserve", "validate", "reserve", 0.4)
    analysis = MethodAnalysis(
        "entry",
        (Phase(nodes=["validate"]), Phase(nodes=["reserve"])),
        unresolved_gates=(gate,),
    )
    question = build_gate_question(gate, method, analysis, frozenset())
    assert question is not None
    client = MagicMock()
    client.complete.return_value = "{truncated"

    assert resolve_execution_phase_gate_batch(client, (question,)) == {}

    assert client.complete.call_count == 3
    diagnostics = capsys.readouterr().err
    assert "retrying 1 unresolved item(s)" in diagnostics
    assert f"result id={question.id} status=UNRESOLVED_MISSING_OR_INVALID" in diagnostics


def test_phase_service_retries_only_malformed_json_line() -> None:
    method = _method()
    gate = UnresolvedGate("validate-reserve", "validate", "reserve", 0.4)
    analysis = MethodAnalysis(
        "entry",
        (Phase(nodes=["validate"]), Phase(nodes=["reserve"])),
        unresolved_gates=(gate,),
    )
    first = build_gate_question(gate, method, analysis, frozenset())
    assert first is not None
    second = replace(first, id="question:second")
    client = MagicMock()
    client.complete.side_effect = [
        json.dumps({"id": first.id, "action": "SPLIT", "confidence": 0.8})
        + '\n{"id":"question:second","action":',
        json.dumps({"id": second.id, "action": "MERGE", "confidence": 0.7}),
    ]

    answers = resolve_execution_phase_gate_batch(client, (first, second))

    assert set(answers) == {first.id, second.id}
    retry = json.loads(client.complete.call_args_list[1].kwargs["user"])
    assert [question["id"] for question in retry["questions"]] == [second.id]


def test_gate_question_marks_conflicting_systematic_evidence() -> None:
    method = _method()
    gate = UnresolvedGate(
        "validate-reserve",
        "validate",
        "reserve",
        1.0,
        ("direct-data-flow", "complete-core-identity-disjoint"),
        "structure-boundary",
    )
    analysis = MethodAnalysis(
        "entry",
        (Phase(nodes=["validate"]), Phase(nodes=["reserve"])),
        unresolved_gates=(gate,),
    )

    question = build_gate_question(gate, method, analysis, frozenset())

    assert question is not None
    expected = _expected_prompt_payload(direct_flow=False)
    expected["boundaryKind"] = "structure-boundary"
    expected["systematicAssessment"].update({
        "reasonCode": "CONFLICTING_EVIDENCE",
        "confidence": 1.0,
        "positiveEvidence": ["direct-data-flow"],
        "contradictoryEvidence": ["complete-core-identity-disjoint"],
    })
    assert question.to_prompt_payload() == expected
