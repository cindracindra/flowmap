from __future__ import annotations

from llm.parsing import correlate_records
from llm.policy import estimate_tokens, token_bounded_batches


def test_token_bounded_batches_measure_the_rendered_payload() -> None:
    items = ["a" * 30, "b" * 30, "c" * 30]
    batches = token_bounded_batches(
        items,
        render=lambda batch: "|".join(batch),
        system="system",
        max_items=10,
        max_input_tokens=15,
    )

    assert batches == [[items[0]], [items[1]], [items[2]]]
    assert estimate_tokens("abc") == 1


def test_json_lines_retains_valid_records_around_malformed_record() -> None:
    raw = (
        '{"id":"first","label":"First Label"}\n'
        '{"id":"broken","label":\n'
        '{"id":"second","label":"Second Label"}'
    )

    parsed = correlate_records(
        raw,
        {"first", "broken", "second"},
        validate=lambda record: record.get("label"),
    )

    assert parsed == {"first": "First Label", "second": "Second Label"}
    assert parsed.parse_failures == {"malformed_json": 1, "missing_id": 1}


def test_consecutive_json_objects_are_recovered_and_reported() -> None:
    parsed = correlate_records(
        '{"id":"first","label":"First Label"}'
        '{"id":"second","label":"Second Label"}',
        {"first", "second"},
        validate=lambda record: record.get("label"),
    )

    assert parsed == {"first": "First Label", "second": "Second Label"}
    assert parsed.parse_failures == {"concatenated_json": 1}


def test_legacy_wrapped_collection_is_not_accepted() -> None:
    parsed = correlate_records(
        '{"labels":[{"id":"first","label":"First Label"}]}',
        {"first"},
        validate=lambda record: record.get("label"),
    )

    assert parsed == {}


def test_duplicate_id_invalidates_only_that_requested_id() -> None:
    parsed = correlate_records(
        '{"id":"first","label":"First Label"}\n'
        '{"id":"first","label":"Changed Label"}\n'
        '{"id":"second","label":"Second Label"}',
        {"first", "second"},
        validate=lambda record: record.get("label"),
        required_keys=frozenset({"id", "label"}),
    )

    assert parsed == {"second": "Second Label"}


def test_output_allowance_limits_batch_size() -> None:
    batches = token_bounded_batches(
        list(range(5)),
        render=lambda batch: str(list(batch)),
        system="system",
        max_items=32,
        max_input_tokens=10_000,
        max_output_tokens=100,
        expected_output_chars_per_item=180,
    )

    assert batches == [[0], [1], [2], [3], [4]]
