"""Reusable syntactic parsing for LLM response contracts."""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Callable
from typing import Any


_JSON_FENCE_RE = re.compile(r"^```(?:json|jsonl)?\s*|\s*```$", re.MULTILINE)


def strip_json_fence(raw: str) -> str:
    return _JSON_FENCE_RE.sub("", raw).strip()


def parse_json_object(raw: str) -> dict[str, Any] | None:
    """Parse one JSON object, returning None for blank or malformed output."""
    try:
        value = json.loads(strip_json_fence(raw))
    except (json.JSONDecodeError, TypeError):
        return None
    return value if isinstance(value, dict) else None


class CorrelatedRecords[T](dict[str, T]):
    """Validated records plus non-sensitive parse-failure counters."""

    def __init__(self, values: dict[str, T], parse_failures: Counter[str]):
        super().__init__(values)
        self.parse_failures = dict(parse_failures)


def parse_json_records(raw: str) -> tuple[list[object], Counter[str]]:
    """Scan independent JSON objects, including adjacent objects without newlines.

    JSON Lines remains the response contract. Adjacent top-level objects are
    recovered because some providers omit the requested line separators. Legacy
    collection wrappers and plain-text label formats are not interpreted.
    """
    text = strip_json_fence(raw)
    if not text:
        return [], Counter()
    decoder = json.JSONDecoder()
    records: list[object] = []
    failures: Counter[str] = Counter()
    cursor = 0
    previous_end: int | None = None
    while cursor < len(text):
        whitespace_start = cursor
        while cursor < len(text) and text[cursor].isspace():
            cursor += 1
        if cursor >= len(text):
            break
        if previous_end is not None and whitespace_start == cursor == previous_end:
            failures["concatenated_json"] += 1
        try:
            value, end = decoder.raw_decode(text, cursor)
        except (json.JSONDecodeError, TypeError):
            failures["malformed_json"] += 1
            next_line = text.find("\n", cursor)
            if next_line < 0:
                break
            cursor = next_line + 1
            previous_end = None
            continue
        records.append(value)
        previous_end = end
        cursor = end
    return records, failures


def correlate_records[T](
    raw: str,
    requested_ids: set[str],
    *,
    validate: Callable[[dict[str, Any]], T | None],
    required_keys: frozenset[str] = frozenset({"id"}),
    exact_keys: bool = False,
    issue: Callable[[str], None] | None = None,
) -> CorrelatedRecords[T]:
    """Validate records independently and correlate them to requested IDs.

    Unknown IDs cannot answer a requested item and are ignored. A duplicate
    requested ID invalidates only that ID, so it is retried with missing and
    malformed requested records while valid neighbours remain resolved.
    """
    results: dict[str, T] = {}
    records, failures = parse_json_records(raw)
    seen_ids: set[str] = set()
    for record in records:
        if not isinstance(record, dict):
            failures["invalid_value"] += 1
            if issue is not None:
                issue("non-object JSON Lines record ignored")
            continue
        record_id = record.get("id")
        if not isinstance(record_id, str):
            failures["invalid_value"] += 1
            if issue is not None:
                issue("response record has no text id; ignored")
            continue
        if record_id not in requested_ids:
            failures["invented_id"] += 1
            if issue is not None:
                issue(
                    f"unknown response id {record_id!r}; ignored; "
                    f"expected one of {sorted(requested_ids)!r}"
                )
            continue
        if record_id in seen_ids:
            results.pop(record_id, None)
            failures["duplicate_id"] += 1
            if issue is not None:
                issue(f"duplicate response id {record_id!r}; retrying that id")
            continue
        seen_ids.add(record_id)
        if not required_keys.issubset(record):
            failures["invalid_value"] += 1
            if issue is not None:
                issue(f"response id {record_id!r} is missing required keys")
            continue
        if exact_keys and set(record) != set(required_keys):
            failures["invalid_value"] += 1
            if issue is not None:
                issue(f"response id {record_id!r} has unexpected keys")
            continue
        result = validate(record)
        if result is not None:
            results[record_id] = result
        else:
            failures["invalid_value"] += 1
    missing_count = len(requested_ids - seen_ids)
    if missing_count:
        failures["missing_id"] += missing_count
    return CorrelatedRecords(results, failures)
