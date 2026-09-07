from __future__ import annotations

import sys
from collections import Counter, deque
from concurrent.futures import ThreadPoolExecutor

from llm.client import LLMClient, LLMError, LLM_MAX_ATTEMPTS
from llm.parsing import correlate_records
from llm.policy import (
    BatchQuery,
    estimate_tokens,
    json_prompt,
    record_batch_report,
    run_batched_items,
)
from llm.prompt import (
    _CLASSIFY_OPERATION_RETRY_PROMPT,
    _CLASSIFY_OPERATION_SYSTEM_PROMPT,
    _LABEL_OPSEQ_BATCH_RETRY_PROMPT,
    _LABEL_OPSEQ_BATCH_SYSTEM_PROMPT,
    operation_assignment_payload,
    operation_label_payload,
)
from llm.settings import (
    LLM_BATCH_SIZES,
    LLM_EXPECTED_OUTPUT_CHARS_PER_ITEM,
    LLM_MAX_CONCURRENT_REQUESTS,
    LLM_TARGET_INPUT_TOKENS,
    LLM_TARGET_OUTPUT_TOKENS,
)
from model import Graph, TopicCluster
from service.phase_label_format import normalise_phase_label, valid_phase_label


_BATCH_SIZES = LLM_BATCH_SIZES
assert len(_BATCH_SIZES) == LLM_MAX_ATTEMPTS


def _issue(topic_id: int | None, message: str) -> None:
    print(f"[operation-sequence-label] topic {topic_id!r}: {message}", file=sys.stderr)


def _parse_label_lines(
    raw: str,
    topic_id: int | None,
    requested_ids: set[str],
) -> dict[str, str]:
    """Parse only the documented independent JSON Lines response format."""
    parsed = correlate_records(
        raw,
        requested_ids,
        validate=lambda record: (
            record["label"] if isinstance(record.get("label"), str) else None
        ),
        required_keys=frozenset({"id", "label"}),
        issue=lambda message: _issue(topic_id, message),
    )
    if len(parsed) < len(requested_ids):
        _issue(
            topic_id,
            f"malformed, duplicate, or missing JSON Lines record; "
            f"preview={raw[:200]!r}",
        )
    return parsed


def classify_operation_topics(
    client: LLMClient,
    operations: dict[str, Graph],
    clusters: list[TopicCluster],
) -> dict[str, int | None]:
    """Assign operations to LLM-formed topics in token-bounded batches."""
    if not operations:
        return {}
    candidates = [cluster for cluster in clusters if cluster.label != -1]
    if not candidates:
        return {operation_id: None for operation_id in operations}
    valid_labels = {cluster.label for cluster in candidates}

    def payload(operation_ids, _resolved):
        return operation_assignment_payload(operation_ids, operations, candidates)

    def validate(record):
        if set(record) != {"id", "group_id"}:
            return None
        group_id = record.get("group_id")
        if group_id is None:
            return -1
        if isinstance(group_id, int) and group_id in valid_labels:
            return group_id
        _issue(None, f"invalid assignment topic id {group_id!r}")
        return None

    def parse(raw: str, requested_ids: set[str]):
        return correlate_records(
            raw,
            requested_ids,
            validate=validate,
            required_keys=frozenset({"id", "group_id"}),
            exact_keys=True,
            issue=lambda message: _issue(None, message),
        )

    resolved = run_batched_items(
        client,
        list(operations),
        item_id=str,
        build_payload=payload,
        parse=parse,
        query=BatchQuery(
            role="small",
            initial_system=_CLASSIFY_OPERATION_SYSTEM_PROMPT,
            retry_system=_CLASSIFY_OPERATION_RETRY_PROMPT,
            call_site="classify_operation_topics",
            batch_sizes=LLM_BATCH_SIZES,
        ),
        issue=lambda message: _issue(None, message),
    )
    return {
        operation_id: None
        if resolved.get(operation_id) in (None, -1)
        else resolved[operation_id]
        for operation_id in operations
    }


def label_operation_sequences_with_llm(
    client: LLMClient,
    operations: dict[str, tuple[Graph, TopicCluster | None]],
) -> dict[str, str | None]:
    """Label operations in topic-aware batches with bounded concurrency."""
    if not operations:
        return {}

    topic_batches: dict[int | None, list[tuple[str, Graph]]] = {}
    clusters: dict[int | None, TopicCluster | None] = {}
    for operation_id, (operation, cluster) in operations.items():
        topic_id = cluster.label if cluster is not None else None
        topic_batches.setdefault(topic_id, []).append((operation_id, operation))
        clusters[topic_id] = cluster

    def label_topic(
        item: tuple[int | None, list[tuple[str, Graph]]],
    ) -> tuple[dict[str, str], int, int, int, dict[str, int]]:
        topic_id, topic_operations = item
        cluster = clusters[topic_id]
        operations_by_id = {
            operation_id: operation
            for operation_id, operation in topic_operations
        }
        resolved: dict[str, str] = {}
        request_batches = 0
        retry_items = 0
        attempts_used = 0
        parse_failures: Counter[str] = Counter()

        def payload(operation_ids: list[str]) -> dict[str, object]:
            return operation_label_payload(
                operation_ids,
                operations_by_id,
                cluster,
                resolved,
            )

        def estimated_tokens(operation_ids: list[str], stage: int) -> int:
            system = (
                _LABEL_OPSEQ_BATCH_SYSTEM_PROMPT
                if stage == 0 else _LABEL_OPSEQ_BATCH_RETRY_PROMPT
            )
            serialized = json_prompt(payload(operation_ids))
            return estimate_tokens(system, serialized)

        def expected_output_tokens(operation_ids: list[str]) -> int:
            return estimate_tokens(
                "x" * (
                    len(operation_ids) * LLM_EXPECTED_OUTPUT_CHARS_PER_ITEM
                )
            )

        initial_batches: list[list[str]] = []
        current: list[str] = []
        for operation_id in operations_by_id:
            candidate = [*current, operation_id]
            too_many = len(candidate) > _BATCH_SIZES[0]
            too_large = estimated_tokens(candidate, 0) > LLM_TARGET_INPUT_TOKENS
            output_too_large = (
                expected_output_tokens(candidate) > LLM_TARGET_OUTPUT_TOKENS
            )
            if current and (too_many or too_large or output_too_large):
                initial_batches.append(current)
                current = [operation_id]
            else:
                current = candidate
        if current:
            initial_batches.append(current)

        work = deque((batch, 0) for batch in initial_batches)
        while work:
            queued_ids, stage = work.popleft()
            pending_ids = [item for item in queued_ids if item not in resolved]
            if not pending_ids:
                continue
            if (
                (
                    estimated_tokens(pending_ids, stage) > LLM_TARGET_INPUT_TOKENS
                    or expected_output_tokens(pending_ids)
                    > LLM_TARGET_OUTPUT_TOKENS
                )
                and len(pending_ids) > 1
            ):
                midpoint = len(pending_ids) // 2
                work.appendleft((pending_ids[midpoint:], stage))
                work.appendleft((pending_ids[:midpoint], stage))
                continue

            response_items: dict[str, str] = {}
            request_batches += 1
            attempts_used = max(attempts_used, stage + 1)
            try:
                raw = client.complete(
                    role="small",
                    system=(
                        _LABEL_OPSEQ_BATCH_SYSTEM_PROMPT
                        if stage == 0 else _LABEL_OPSEQ_BATCH_RETRY_PROMPT
                    ),
                    user=json_prompt(payload(pending_ids)),
                    max_tokens=LLM_TARGET_OUTPUT_TOKENS,
                    call_site="label_operation_sequences",
                )
                if raw.strip():
                    response_items = _parse_label_lines(
                        raw, topic_id, set(pending_ids)
                    )
                    parse_failures.update(
                        getattr(response_items, "parse_failures", {})
                    )
            except LLMError as exc:
                lowered = str(exc).casefold()
                context_overflow = "context length" in lowered or (
                    "input" in lowered and "longer than" in lowered
                )
                if context_overflow and len(pending_ids) > 1:
                    midpoint = len(pending_ids) // 2
                    _issue(
                        topic_id,
                        f"context limit split {len(pending_ids)} operations into "
                        f"{midpoint} and {len(pending_ids) - midpoint} instead of "
                        "retrying unchanged",
                    )
                    work.appendleft((pending_ids[midpoint:], stage))
                    work.appendleft((pending_ids[:midpoint], stage))
                    continue
                _issue(topic_id, f"provider error on attempt {stage + 1}: {exc}")

            requested_ids = set(pending_ids)
            used_labels = {label.casefold() for label in resolved.values()}
            attempt_labels: dict[str, str] = {}
            for operation_id, raw_label in response_items.items():
                if operation_id not in requested_ids or operation_id in attempt_labels:
                    continue
                label = normalise_phase_label(raw_label)
                normalized = label.casefold()
                if not valid_phase_label(label) or normalized in used_labels:
                    parse_failures["invalid_value"] += 1
                    continue
                attempt_labels[operation_id] = label
                used_labels.add(normalized)
            resolved.update(attempt_labels)

            unresolved = [
                operation_id
                for operation_id in pending_ids
                if operation_id not in attempt_labels
            ]
            if unresolved and stage + 1 < len(_BATCH_SIZES):
                _issue(
                    topic_id,
                    f"attempt {stage + 1} left {len(unresolved)} operation(s) "
                    "unresolved; retrying",
                )
                next_stage = stage + 1
                retry_items += len(unresolved)
                next_size = _BATCH_SIZES[next_stage]
                chunks = [
                    unresolved[start : start + next_size]
                    for start in range(0, len(unresolved), next_size)
                ]
                for chunk in reversed(chunks):
                    work.appendleft((chunk, next_stage))

        return (
            resolved,
            request_batches,
            retry_items,
            attempts_used,
            dict(parse_failures),
        )

    batches = list(topic_batches.items())
    worker_count = min(LLM_MAX_CONCURRENT_REQUESTS, len(batches))
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        topic_results = list(executor.map(label_topic, batches))
        labels = {
            operation_id: label
            for batch, _requests, _retries, _attempts, _failures in topic_results
            for operation_id, label in batch.items()
        }
    record_batch_report(
        client,
        call_site="label_operation_sequences",
        requested_items=len(operations),
        resolved_items=len(labels),
        unresolved_items=len(operations) - len(labels),
        oversized_items=0,
        attempts_used=max((result[3] for result in topic_results), default=0),
        request_batches=sum(result[1] for result in topic_results),
        retry_items=sum(result[2] for result in topic_results),
        parse_failures=dict(sum(
            (Counter(result[4]) for result in topic_results), Counter()
        )),
    )
    return {operation_id: labels.get(operation_id) for operation_id in operations}
