"""UPDATED Stage 1: the operations that must not appear in any phase.

Some operations exist only to raise an exception rather than to carry out
business work. If they became phases they would be labelled as though they were
steps in the process, so they are removed from consideration before segmentation
begins.
"""

from __future__ import annotations

from typing import Literal

from model import Graph


# Why an operation is kept out of every phase.
# `exception-mechanic`: constructs the exception being thrown. 
# `in-throwing-arm`: an ordinary operation inside a failing branch. 
ExclusionReason = Literal["exception-mechanic", "in-throwing-arm"]


def _exception_constructor(callee: str | None, receiver_type: str | None) -> bool:
    """True when this call constructs an exception type."""
    if callee and ".<init>" not in callee:
        return False
    candidates = []
    if callee:
        candidates.append(callee.split(":", 1)[0].split(".<init>", 1)[0])
    if receiver_type:
        candidates.append(receiver_type)
    return any(
        value.rsplit(".", 1)[-1].endswith(("Exception", "Error", "Throwable"))
        for value in candidates
    )


def _throwing_arms(graph: Graph) -> set[tuple[str, str]]:
    """Every arm recorded as ending in a throw, as (groupId, armLabel)."""
    return {
        (group.id, arm.label)
        for group in graph.branchGroups
        for arm in group.arms
        if arm.terminus == "throw"
    }


def find_excluded_operations(graph: Graph) -> dict[str, ExclusionReason]:
    """UPDATED Stage 1: which operations are kept out of every phase, and why.
    * Rule 1 is surgical, and reaches an unconditional throw, which has no arm.
    * Rule 2 is wholesale, and removes the plumbing *feeding* the constructor
      without having to reason about names or data flow.
    """
    excluded: dict[str, ExclusionReason] = {}

    throwing_arms = _throwing_arms(graph)
    
    if throwing_arms:
        for node in graph.nodes:
            if node.type != "call":
                continue
            if any(
                (tag.groupId, tag.armLabel) in throwing_arms
                for tag in node.branchArms
            ):
                excluded[node.id] = "in-throwing-arm"

    for node in graph.nodes:
        if node.type != "call" or not node.deadEnd:
            continue
        features = graph.semanticFeatures.get(node.id)
        if _exception_constructor(
            node.calleeFullName, features.receiverType if features else None
        ):
            excluded[node.id] = "exception-mechanic"

    return excluded
