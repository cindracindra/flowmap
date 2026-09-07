"""Shared reliability, batching, and concurrency policy for LLM requests."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from .client import LLMClient, LLMError, Role
from .settings import (
    LLM_MAX_ATTEMPTS,
    LLM_MAX_CONCURRENT_REQUESTS,
    LLM_EXPECTED_OUTPUT_CHARS_PER_ITEM,
    LLM_TARGET_INPUT_TOKENS,
    LLM_TARGET_OUTPUT_TOKENS,
)

_ESTIMATED_BYTES_PER_TOKEN = 3


def is_context_length_error(error: BaseException) -> bool:
    message = str(error).casefold()
    return "context length" in message or "context window" in message or (
        "input" in message and "longer than" in message
    )


def estimate_tokens(*parts: str) -> int:
    """Conservatively estimate tokens for already-rendered request text."""
    byte_count = sum(len(part.encode("utf-8")) for part in parts)
    return (byte_count + _ESTIMATED_BYTES_PER_TOKEN - 1) // _ESTIMATED_BYTES_PER_TOKEN


def json_prompt(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def record_batch_report(client: LLMClient, **event: object) -> None:
    recorder = getattr(client, "record_batch_report", None)
    if callable(recorder):
        recorder(event)


def token_bounded_batches[Item](
    items: Sequence[Item],
    *,
    render: Callable[[Sequence[Item]], str],
    system: str,
    max_items: int,
    max_input_tokens: int = LLM_TARGET_INPUT_TOKENS,
    max_output_tokens: int = LLM_TARGET_OUTPUT_TOKENS,
    expected_output_chars_per_item: int = LLM_EXPECTED_OUTPUT_CHARS_PER_ITEM,
) -> list[list[Item]]:
    """Greedily repack items against both input and expected output size."""
    if max_items < 1:
        raise ValueError("max_items must be positive")
    batches: list[list[Item]] = []
    current: list[Item] = []
    for item in items:
        candidate = [*current, item]
        input_oversized = (
            estimate_tokens(system, render(candidate)) > max_input_tokens
        )
        output_oversized = (
            estimate_tokens("x" * (len(candidate) * expected_output_chars_per_item))
            > max_output_tokens
        )
        if current and (
            len(candidate) > max_items or input_oversized or output_oversized
        ):
            batches.append(current)
            current = [item]
        else:
            current = candidate
    if current:
        batches.append(current)
    return batches


@dataclass(frozen=True)
class BatchQuery:
    role: Role
    initial_system: str
    retry_system: str
    call_site: str
    batch_sizes: tuple[int, int, int]
    max_tokens: int = LLM_TARGET_OUTPUT_TOKENS
    max_input_tokens: int = LLM_TARGET_INPUT_TOKENS
    json_object: bool = False
    expected_output_chars_per_item: int = LLM_EXPECTED_OUTPUT_CHARS_PER_ITEM


@dataclass(frozen=True)
class Query:
    role: Role
    initial_system: str
    retry_system: str
    call_site: str
    max_tokens: int = LLM_TARGET_OUTPUT_TOKENS
    json_object: bool = False
    max_input_tokens: int = LLM_TARGET_INPUT_TOKENS


def run_query[Result](
    client: LLMClient,
    user: str,
    *,
    parse: Callable[[str], Result | None],
    query: Query,
    issue: Callable[[str], None] | None = None,
) -> Result | None:
    """Apply the common three-attempt policy to a single logical request."""
    if estimate_tokens(query.initial_system, user) > query.max_input_tokens:
        if issue is not None:
            issue("request exceeds the input-token budget")
        record_batch_report(
            client, call_site=query.call_site, requested_items=1,
            resolved_items=0, unresolved_items=1, oversized_items=1,
            attempts_used=0, request_batches=0, retry_items=0,
        )
        return None
    attempts_used = 0
    for attempt in range(LLM_MAX_ATTEMPTS):
        attempts_used = attempt + 1
        try:
            raw = client.complete(
                role=query.role,
                system=(
                    query.initial_system if attempt == 0 else query.retry_system
                ),
                user=user,
                max_tokens=query.max_tokens,
                json_object=query.json_object,
                call_site=query.call_site,
            )
        except LLMError as exc:
            if issue is not None:
                issue(f"provider error on attempt {attempt + 1}: {exc}")
            if is_context_length_error(exc):
                break
            continue
        result = parse(raw)
        if result is not None:
            record_batch_report(
                client, call_site=query.call_site, requested_items=1,
                resolved_items=1, unresolved_items=0, oversized_items=0,
                attempts_used=attempts_used, request_batches=attempts_used,
                retry_items=max(0, attempts_used - 1),
            )
            return result
        if issue is not None:
            issue(f"invalid response on attempt {attempt + 1}")
    record_batch_report(
        client, call_site=query.call_site, requested_items=1,
        resolved_items=0, unresolved_items=1, oversized_items=0,
        attempts_used=attempts_used, request_batches=attempts_used,
        retry_items=max(0, attempts_used - 1),
    )
    return None


def run_batched_items[Item, Result](
    client: LLMClient,
    items: Sequence[Item],
    *,
    item_id: Callable[[Item], str],
    build_payload: Callable[[Sequence[Item], Mapping[str, Result]], object],
    parse: Callable[[str, set[str]], Mapping[str, Result]],
    query: BatchQuery,
    issue: Callable[[str], None] | None = None,
) -> dict[str, Result]:
    """Resolve independent items with shared partial retry semantics.

    Valid results survive later parse/provider failures. Every attempt repacks
    only unresolved items against the complete prompt and payload size, and all
    independent batches use the common four-request concurrency ceiling.
    """
    resolved: dict[str, Result] = {}
    pending = list(items)
    oversized_ids: set[str] = set()
    attempts_used = 0
    request_batches = 0
    retry_items = 0
    parse_failures: Counter[str] = Counter()
    for attempt in range(LLM_MAX_ATTEMPTS):
        if not pending:
            break
        attempts_used = attempt + 1
        if attempt:
            retry_items += len(pending)
        system = query.initial_system if attempt == 0 else query.retry_system

        def render(batch: Sequence[Item]) -> str:
            return json_prompt(build_payload(batch, resolved))

        batches = token_bounded_batches(
            pending,
            render=render,
            system=system,
            max_items=query.batch_sizes[attempt],
            max_input_tokens=query.max_input_tokens,
            max_output_tokens=query.max_tokens,
            expected_output_chars_per_item=query.expected_output_chars_per_item,
        )
        requestable: list[list[Item]] = []
        for batch in batches:
            if len(batch) == 1 and estimate_tokens(system, render(batch)) > query.max_input_tokens:
                oversized_id = item_id(batch[0])
                oversized_ids.add(oversized_id)
                if issue is not None:
                    issue(f"item {oversized_id!r} exceeds the input-token budget")
            else:
                requestable.append(batch)
        batches = requestable
        request_batches += len(batches)
        if not batches:
            pending = [item for item in pending if item_id(item) not in oversized_ids]
            continue

        def request(batch: list[Item]) -> Mapping[str, Result]:
            requested_ids = {item_id(item) for item in batch}
            try:
                raw = client.complete(
                    role=query.role,
                    system=system,
                    user=render(batch),
                    max_tokens=query.max_tokens,
                    json_object=query.json_object,
                    call_site=query.call_site,
                )
            except LLMError as exc:
                if issue is not None:
                    issue(f"provider error on attempt {attempt + 1}: {exc}")
                return {}
            return parse(raw, requested_ids)

        with ThreadPoolExecutor(
            max_workers=min(LLM_MAX_CONCURRENT_REQUESTS, len(batches))
        ) as executor:
            for parsed in executor.map(request, batches):
                parse_failures.update(getattr(parsed, "parse_failures", {}))
                for result_id, result in parsed.items():
                    if result_id not in resolved:
                        resolved[result_id] = result

        pending = [
            item for item in pending
            if item_id(item) not in resolved and item_id(item) not in oversized_ids
        ]
        if pending and attempt < LLM_MAX_ATTEMPTS - 1 and issue is not None:
            issue(f"retrying {len(pending)} unresolved item(s)")
    record_batch_report(
        client,
        call_site=query.call_site,
        requested_items=len(items),
        resolved_items=len(resolved),
        unresolved_items=len(items) - len(resolved),
        oversized_items=len(oversized_ids),
        attempts_used=attempts_used,
        request_batches=request_batches,
        retry_items=retry_items,
        parse_failures=dict(parse_failures),
    )
    return resolved
