"""Batched LLM service for stable method-phase label subjects."""

from __future__ import annotations

import sys

from domain.method_phase_label import LabelSubject, MethodPhaseLabelRequest
from llm.client import LLMClient
from llm.parsing import correlate_records
from llm.policy import BatchQuery, run_batched_items
from llm.prompt import (
    _LABEL_METHOD_PHASES_RETRY_PROMPT,
    _LABEL_METHOD_PHASES_SYSTEM_PROMPT,
    method_phase_label_payload,
)
from llm.settings import LLM_BATCH_SIZES
from service.phase_label_format import normalise_phase_label, valid_phase_label


_QUERY = BatchQuery(
    role="small",
    initial_system=_LABEL_METHOD_PHASES_SYSTEM_PROMPT,
    retry_system=_LABEL_METHOD_PHASES_RETRY_PROMPT,
    call_site="label_method_phases",
    batch_sizes=LLM_BATCH_SIZES,
)


def _issue(message: str) -> None:
    print(f"[method-phase-label] {message}", file=sys.stderr)


def _semantic_evidence_count(subject: LabelSubject) -> int:
    return sum(
        1
        for phase in subject["phaseEvidence"]
        if any(phase.get("coreSignature", {}).values())
        or any(
            operation.get("callee") or operation.get("code")
            for operation in phase["operations"]
        )
    )


def _preflight(subjects: list[LabelSubject]) -> list[LabelSubject]:
    valid: list[LabelSubject] = []
    seen: set[str] = set()
    for subject in subjects:
        subject_id = subject.get("id", "")
        if not subject_id or subject_id in seen:
            _issue(f"invalid or duplicate subject id {subject_id!r}; skipped")
            continue
        seen.add(subject_id)
        evidence_ids = [
            phase.get("phaseId") for phase in subject.get("phaseEvidence", [])
        ]
        if (
            not evidence_ids
            or any(not phase_id for phase_id in evidence_ids)
            or len(evidence_ids) != len(set(evidence_ids))
        ):
            _issue(f"{subject_id}: invalid phaseEvidence identifiers; skipped")
            continue
        if _semantic_evidence_count(subject) == 0:
            _issue(f"{subject_id}: no semantic operation evidence; skipped")
            continue
        valid.append(subject)
    return valid


def _parse_labels(raw: str, requested_ids: set[str]) -> dict[str, str]:
    if not raw.strip():
        _issue("blank batch response")
        return {}
    def validate(item: dict) -> str | None:
        subject_id = item["id"]
        raw_label = item.get("label")
        if not isinstance(raw_label, str):
            _issue(f"{subject_id}: label is not text")
            return None
        label = normalise_phase_label(raw_label)
        if not valid_phase_label(label):
            _issue(f"{subject_id}: invalid 2-6 word label {label!r}")
            return None
        return label

    return correlate_records(
        raw,
        requested_ids,
        validate=validate,
        required_keys=frozenset({"id", "label"}),
        issue=_issue,
    )


def label_method_phases(
    client: LLMClient,
    request: MethodPhaseLabelRequest,
) -> dict[str, str]:
    """Label subjects in bounded batches, progressively isolating failures.

    A large response can be cut off in the middle of its JSON object. Small
    initial batches limit that risk; unresolved subjects are retried in still
    smaller batches, ending with one request per subject so a malformed
    response cannot discard labels for unrelated phases.
    """
    subjects = _preflight(request.get("subjects", []))
    if not subjects:
        return {}
    schema_version = request.get("schemaVersion", "execution-phase-label-v2")
    return run_batched_items(
        client,
        subjects,
        item_id=lambda subject: subject["id"],
        build_payload=lambda batch, _resolved: method_phase_label_payload(
            batch, schema_version=schema_version
        ),
        parse=_parse_labels,
        query=_QUERY,
        issue=_issue,
    )
