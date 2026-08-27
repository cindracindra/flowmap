"""Batched LLM service for stable method-phase label subjects."""

from __future__ import annotations

import json
import re
import sys

from domain.method_phase_label import LabelSubject, MethodPhaseLabelRequest
from llm.client import LLMClient, LLMError
from llm.prompt import _LABEL_METHOD_PHASES_SYSTEM_PROMPT
from service.phase_label_format import normalise_phase_label, valid_phase_label


_JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)
_RETRY_SUFFIX = (
    " This is a corrective retry containing only subjects whose labels were "
    "missing or invalid. Return all requested IDs exactly, with valid labels "
    "containing 2-6 whitespace-separated tokens and never more than 6."
)
_BATCH_SIZES = (8, 4, 1)


def _issue(message: str) -> None:
    print(f"[method-phase-label] {message}", file=sys.stderr)


def _semantic_evidence_count(subject: LabelSubject) -> int:
    return sum(
        1
        for phase in subject["phaseEvidence"]
        for operation in phase["operations"]
        if any(operation.get(field) for field in (
            "callee", "code", "receiver", "arguments", "inputs", "fieldsRead",
            "fieldsWritten", "domainTypes", "methodTerms",
        ))
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
        phase_ids = subject.get("phaseIds", [])
        evidence_ids = [phase.get("phaseId") for phase in subject.get("phaseEvidence", [])]
        if not phase_ids or phase_ids != evidence_ids:
            _issue(f"{subject_id}: phaseIds do not match phaseEvidence; skipped")
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
    try:
        payload = json.loads(_JSON_FENCE_RE.sub("", raw).strip())
    except (json.JSONDecodeError, TypeError) as exc:
        _issue(f"invalid JSON response: {exc}")
        return {}
    items = payload.get("labels", []) if isinstance(payload, dict) else []
    if not isinstance(items, list):
        _issue("response labels is not a list")
        return {}
    labels: dict[str, str] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        subject_id = item.get("id")
        raw_label = item.get("label")
        if subject_id not in requested_ids:
            _issue(f"unknown response id {subject_id!r}; ignored")
            continue
        if subject_id in labels:
            _issue(f"duplicate response id {subject_id!r}; ignored")
            continue
        if not isinstance(raw_label, str):
            _issue(f"{subject_id}: label is not text")
            continue
        label = normalise_phase_label(raw_label)
        if not valid_phase_label(label):
            _issue(f"{subject_id}: invalid 2-6 word label {label!r}")
            continue
        labels[subject_id] = label
    return labels


def _chunks(subjects: list[LabelSubject], size: int):
    for start in range(0, len(subjects), size):
        yield subjects[start:start + size]


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
    resolved: dict[str, str] = {}
    pending = subjects
    for attempt, batch_size in enumerate(_BATCH_SIZES):
        for chunk in _chunks(pending, batch_size):
            batch: MethodPhaseLabelRequest = {
                "schemaVersion": "method-phase-label-v1",
                "subjects": chunk,
            }
            try:
                raw = client.complete(
                    role="small",
                    system=(
                        _LABEL_METHOD_PHASES_SYSTEM_PROMPT
                        if attempt == 0
                        else _LABEL_METHOD_PHASES_SYSTEM_PROMPT + _RETRY_SUFFIX
                    ),
                    user=json.dumps(batch, ensure_ascii=False),
                    # Long correlation IDs make the JSON considerably larger
                    # than the labels themselves. Reserve enough output for
                    # complete objects even when every label uses six words.
                    max_tokens=max(512, min(2048, 128 * len(chunk))),
                    json_object=True,
                    call_site="label_method_phases",
                )
            except LLMError as exc:
                _issue(f"provider error on attempt {attempt + 1}: {exc}")
                continue
            requested_ids = {subject["id"] for subject in chunk}
            resolved.update(_parse_labels(raw, requested_ids))

        unresolved_ids = {subject["id"] for subject in pending} - resolved.keys()
        if not unresolved_ids:
            break
        if attempt < len(_BATCH_SIZES) - 1:
            _issue(f"retrying {len(unresolved_ids)} unresolved subjects")
            pending = [subject for subject in pending if subject["id"] in unresolved_ids]
        else:
            for subject_id in sorted(unresolved_ids):
                _issue(f"{subject_id}: unresolved after retry")
    return resolved
