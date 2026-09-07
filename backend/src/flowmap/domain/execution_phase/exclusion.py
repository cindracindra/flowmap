"""Stage 1: identify operations that must not appear in phases."""

from __future__ import annotations

from typing import Literal

from model import Graph

ExclusionReason = Literal["exception-mechanic", "in-throwing-arm"]


def _exception_constructor(callee: str | None, receiver_type: str | None) -> bool:
    """Return whether a call constructs an exception type."""
    if callee and ".<init>" not in callee:
        return False

    candidates: list[str] = []
    if callee:
        candidates.append(callee.split(":", 1)[0].split(".<init>", 1)[0])
    if receiver_type:
        candidates.append(receiver_type)

    return any(
        value.rsplit(".", 1)[-1].endswith(("Exception", "Error", "Throwable"))
        for value in candidates
    )


def _throwing_arms(graph: Graph) -> set[tuple[str, str]]:
    """Return arms whose authoritative outcomes are exclusively throws."""
    return {
        (group.id, arm.label)
        for group in graph.branchGroups
        for arm in group.arms
        if {exit_.kind for exit_ in arm.exits} == {"throw"}
    }


def find_excluded_operations(graph: Graph) -> dict[str, ExclusionReason]:
    """Return phase-ineligible call ids and the reason each is excluded."""
    throwing_arms = _throwing_arms(graph)
    excluded: dict[str, ExclusionReason] = {
        node.id: "in-throwing-arm"
        for node in graph.nodes
        if node.type == "call"
        and any((tag.groupId, tag.armLabel) in throwing_arms for tag in node.branchArms)
    }

    for node in graph.nodes:
        if node.id not in excluded:
            continue
        features = graph.semanticFeatures.get(node.id)
        if _exception_constructor(
            node.calleeFullName,
            features.receiverType if features else None,
        ):
            excluded[node.id] = "exception-mechanic"

    return excluded
