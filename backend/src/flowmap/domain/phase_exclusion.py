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
    """Return arm identities known to terminate by throwing.

    Filtered graphs identify branch membership on each node. A tagged dead-end
    call therefore identifies its throwing arm without requiring a separate
    BranchGroup representation. BranchGroup metadata is also accepted while
    older filtered-graph producers and fixtures still provide it.
    """
    # ``branchArms`` is ordered from the outermost arm to the innermost one.
    # A dead end nested inside several branches proves only that its immediate
    # (innermost) arm throws; treating every containing arm as throwing would
    # incorrectly exclude ordinary work elsewhere in an outer arm.
    arms = {
        (node.branchArms[-1].groupId, node.branchArms[-1].armLabel)
        for node in graph.nodes
        if node.type == "call" and node.deadEnd and node.branchArms
    }
    arms.update(
        (group.id, arm.label)
        for group in graph.branchGroups
        for arm in group.arms
        if arm.terminus == "throw"
    )
    return arms


def find_excluded_operations(graph: Graph) -> dict[str, ExclusionReason]:
    """Return phase-ineligible call ids and the reason each is excluded.

    Every call in a throwing arm is excluded first. Dead-end exception
    constructors are then assigned the more specific ``exception-mechanic``
    reason, including unconditional throws that have no branch-arm tag.
    """
    throwing_arms = _throwing_arms(graph)
    excluded: dict[str, ExclusionReason] = {
        node.id: "in-throwing-arm"
        for node in graph.nodes
        if node.type == "call"
        and any(
            (tag.groupId, tag.armLabel) in throwing_arms
            for tag in node.branchArms
        )
    }

    for node in graph.nodes:
        if node.type != "call" or not node.deadEnd:
            continue
        features = graph.semanticFeatures.get(node.id)
        if _exception_constructor(
            node.calleeFullName,
            features.receiverType if features else None,
        ):
            excluded[node.id] = "exception-mechanic"

    return excluded
