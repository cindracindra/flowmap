from __future__ import annotations

import sys

from llm.client import LLMClient
from llm.parsing import correlate_records
from llm.policy import BatchQuery, run_batched_items

from domain.execution_phase.resolution import (
    GateAnswer,
    UnresolvedGateQuestion,
)
from llm.prompt import (
    _PHASE_GATE_RETRY_PROMPT,
    _PHASE_GATE_SYSTEM_PROMPT,
    execution_phase_gate_payload,
)
from llm.settings import LLM_BATCH_SIZES


_GATE_QUERY = BatchQuery(
    role="small",
    initial_system=_PHASE_GATE_SYSTEM_PROMPT,
    retry_system=_PHASE_GATE_RETRY_PROMPT,
    call_site="resolve_execution_phase_gate_batch",
    batch_sizes=LLM_BATCH_SIZES,
)


def _phase_gate_issue(message: str) -> None:
    print(f"[phase-gate] {message}", file=sys.stderr)


def resolve_execution_phase_gate_batch(
    client: LLMClient,
    questions: tuple[UnresolvedGateQuestion, ...],
) -> dict[str, GateAnswer]:
    """Resolve one batch of compact execution-phase gate questions."""
    if not questions:
        return {}
    question_ids = {question.id for question in questions}

    def parse(raw: str, requested_ids: set[str]) -> dict[str, GateAnswer]:
        def validate(decision: dict) -> GateAnswer | None:
            question_id = decision.get("id")
            action = decision.get("action")
            if action not in {"MERGE", "SPLIT"}:
                _phase_gate_issue(
                    f"result id={question_id} status=INVALID_ACTION action={action!r}"
                )
                return None
            confidence = decision.get("confidence")
            if not isinstance(confidence, (int, float)) or isinstance(confidence, bool):
                return None
            if not 0.0 <= float(confidence) <= 1.0:
                return None
            return (action, confidence, ("llm-semantic-decision",))

        return correlate_records(
            raw,
            requested_ids,
            validate=validate,
            required_keys=frozenset({"id", "action", "confidence"}),
            exact_keys=True,
            issue=_phase_gate_issue,
        )

    answers = run_batched_items(
        client,
        questions,
        item_id=lambda question: question.id,
        build_payload=lambda batch, _resolved: execution_phase_gate_payload(batch),
        parse=parse,
        query=_GATE_QUERY,
        issue=_phase_gate_issue,
    )
    for question_id, (action, confidence, _evidence) in answers.items():
        _phase_gate_issue(
            f"result id={question_id} action={action} confidence={confidence:.2f}"
        )

    missing_ids = question_ids - answers.keys()
    for question_id in sorted(missing_ids):
        _phase_gate_issue(f"result id={question_id} status=UNRESOLVED_MISSING_OR_INVALID")
    merge_count = sum(answer[0] == "MERGE" for answer in answers.values())
    split_count = sum(answer[0] == "SPLIT" for answer in answers.values())
    _phase_gate_issue(
        f"batch summary requested={len(question_ids)} merge={merge_count} "
        f"split={split_count} unresolved={len(missing_ids)}"
    )
    return answers
