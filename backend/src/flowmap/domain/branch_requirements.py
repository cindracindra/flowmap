from __future__ import annotations

from collections.abc import Iterable

from model import BranchRequirement


def merge_branch_requirements(
    *requirement_lists: Iterable[BranchRequirement],
) -> list[BranchRequirement] | None:
    """Union route guards, rejecting two selected arms of one group.

    Order is stable: the first occurrence of each group determines its output
    position. ``None`` means the combined route is statically impossible.
    """
    selected: dict[str, str] = {}
    merged: list[BranchRequirement] = []
    for requirements in requirement_lists:
        for requirement in requirements:
            previous = selected.get(requirement.groupId)
            if previous is not None and previous != requirement.armLabel:
                return None
            if previous is None:
                selected[requirement.groupId] = requirement.armLabel
                merged.append(requirement)
    return merged
