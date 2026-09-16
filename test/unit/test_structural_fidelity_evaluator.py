from pathlib import Path

from evaluation.run_structural_fidelity import (
    maven_command,
    parse_project_values,
    reachable_node_ids,
    resolve_entry_selectors,
    structural_category_breakdown,
)


def test_reachable_nodes_follow_sequence_and_invocation_adjacency() -> None:
    adjacency = {
        "entry-a": ["call-c"],
        "call-c": ["entry-c", "after-c"],
        "entry-c": ["exit-c"],
        "entry-b": ["exit-b"],
    }

    assert reachable_node_ids(adjacency, ["entry-a"]) == {
        "entry-a",
        "call-c",
        "entry-c",
        "exit-c",
        "after-c",
    }


def test_entry_selector_can_resolve_exact_signature() -> None:
    entries = [
        {"id": "one", "calleeFullName": "example.Service.run:void()"},
        {"id": "two", "calleeFullName": "example.Service.run:void(int)"},
    ]

    ids, resolutions = resolve_entry_selectors(
        entries, ["example.Service.run:void(int)"]
    )

    assert ids == {"two"}
    assert resolutions[0]["status"] == "resolved"
    assert resolutions[0]["matched_entry_count"] == 1


def test_signature_free_selector_includes_all_overloads() -> None:
    entries = [
        {"id": "one", "calleeFullName": "example.Service.run:void()"},
        {"id": "two", "calleeFullName": "example.Service.run:void(int)"},
        {"id": "other", "calleeFullName": "example.Service.stop:void()"},
    ]

    ids, resolutions = resolve_entry_selectors(entries, ["example.Service.run"])

    assert ids == {"one", "two"}
    assert resolutions[0]["matched_entry_count"] == 2


def test_unresolved_entry_is_reported_and_reaches_nothing() -> None:
    entries = [
        {"id": "one", "calleeFullName": "example.Service.run:void()"},
    ]

    ids, resolutions = resolve_entry_selectors(entries, ["example.Missing.run"])

    assert ids == set()
    assert resolutions[0]["status"] == "unresolved"
    assert reachable_node_ids({}, ids) == set()


def test_fixture_test_selectors_are_grouped_and_passed_to_maven() -> None:
    selectors = parse_project_values(
        ["sample=FeatureTest#first", "sample=FeatureTest#second"],
        "--test-selector",
    )

    command = maven_command(
        Path("."),
        "0.8.12",
        executable="mvn",
        test_selectors=selectors["sample"],
    )

    assert "-Dtest=FeatureTest#first,FeatureTest#second" in command


def test_category_breakdown_separates_missing_from_disconnected_methods() -> None:
    rows = [
        {
            "method_category": "constructor",
            "represented_by_flowmap": False,
            "reachable_from_fixture_entries": False,
            "entry_to_exit_route": None,
        },
        {
            "method_category": "constructor",
            "represented_by_flowmap": True,
            "reachable_from_fixture_entries": False,
            "entry_to_exit_route": True,
        },
        {
            "method_category": "constructor",
            "represented_by_flowmap": True,
            "reachable_from_fixture_entries": True,
            "entry_to_exit_route": False,
        },
    ]

    breakdown = structural_category_breakdown(rows, reachability_enabled=True)
    constructors = next(row for row in breakdown if row["category"] == "constructor")

    assert constructors["covered_methods"] == 3
    assert constructors["unrepresented_methods"] == 1
    assert constructors["represented_but_unreachable_methods"] == 1
    assert constructors["reachable_methods"] == 1
    assert constructors["entry_to_exit_route_failures"] == 1
