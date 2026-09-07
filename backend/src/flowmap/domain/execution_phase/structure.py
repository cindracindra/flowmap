"""Build nested phase-analysis structure from canonical method definitions."""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from typing import TypeAlias

from domain.execution_phase.exclusion import ExclusionReason
from domain.phase_topology import method_call_sequence_pairs
from model import MethodDefinition


ArmPath = tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class LinearStructure:
    """A maximal execution-ordered call run without a fork or merge."""
    node_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class BranchStructure:
    """A branch region containing its alternative, possibly nested arms."""
    group_id: str
    arms: tuple[tuple[Structure, ...], ...]


Structure: TypeAlias = LinearStructure | BranchStructure


@dataclass(frozen=True, slots=True)
class MethodStructure:
    method_entry_id: str
    structures: tuple[Structure, ...]


def _linear_runs(
    members: set[str],
    outgoing: dict[str, list[str]],
    incoming: dict[str, list[str]],
    order: dict[str, int],
) -> list[tuple[str, ...]]:
    """Return maximal one-predecessor/one-successor runs within ``members``."""
    visited: set[str] = set()

    def successor(node_id: str) -> str | None:
        targets = outgoing[node_id]
        if len(targets) != 1:
            return None
        target = targets[0]
        if target not in members or len(incoming[target]) != 1:
            return None
        return target

    def consume(start: str) -> tuple[str, ...]:
        run: list[str] = []
        current: str | None = start
        while current is not None and current not in visited:
            visited.add(current)
            run.append(current)
            current = successor(current)
        return tuple(run)

    starts = sorted(
        (
            node_id
            for node_id in members
            if len(incoming[node_id]) != 1
            or incoming[node_id][0] not in members
            or successor(incoming[node_id][0]) != node_id
        ),
        key=order.__getitem__,
    )
    runs = [consume(start) for start in starts if start not in visited]

    # A closed cycle has no degree-defined starting node.
    for node_id in sorted(members - visited, key=order.__getitem__):
        runs.append(consume(node_id))
    return runs


def _cfg_traversal_rank(
    call_ids: set[str],
    outgoing: dict[str, list[str]],
    incoming: dict[str, list[str]],
    serialization_rank: dict[str, int],
) -> dict[str, int]:
    """Assign deterministic breadth-first order using CFG successor order."""
    starts = sorted(
        (
            node_id
            for node_id in call_ids
            if not any(source in call_ids for source in incoming[node_id])
        ),
        key=serialization_rank.__getitem__,
    )
    if not starts and call_ids:
        starts = [min(call_ids, key=serialization_rank.__getitem__)]

    order: dict[str, int] = {}
    pending = deque(starts)
    while pending:
        node_id = pending.popleft()
        if node_id in order:
            continue
        order[node_id] = len(order)
        pending.extend(target for target in outgoing[node_id] if target not in order)

    for node_id in sorted(call_ids - order.keys(), key=serialization_rank.__getitem__):
        order[node_id] = len(order)
    return order


def build_method_structure(
    method: MethodDefinition,
    excluded: dict[str, ExclusionReason] | None = None,
) -> MethodStructure:
    """Build one method's nested linear/branch structure."""
    excluded_ids = set(excluded or ())
    calls = [node for node in method.nodes if node.type == "call"]
    call_ids = {node.id for node in calls}
    serialization_rank = {node.id: index for index, node in enumerate(calls)}
    arm_path_by_call = {
        node.id: tuple((ref.groupId, ref.armLabel) for ref in node.branchArms)
        for node in calls
    }

    outgoing: dict[str, list[str]] = defaultdict(list)
    incoming: dict[str, list[str]] = defaultdict(list)
    for source, target in method_call_sequence_pairs(method):
        outgoing[source].append(target)
        incoming[target].append(source)

    order = _cfg_traversal_rank(call_ids, outgoing, incoming, serialization_rank)
    eligible_ids = call_ids - excluded_ids

    # Include labels from excluded calls so an alternative arm does not vanish
    # merely because all of its operations were phase-ineligible.
    arm_labels: dict[tuple[ArmPath, str], set[str]] = defaultdict(set)
    for path in arm_path_by_call.values():
        prefix: ArmPath = ()
        for group_id, label in path:
            arm_labels[(prefix, group_id)].add(label)
            prefix = (*prefix, (group_id, label))

    def below(prefix: ArmPath) -> set[str]:
        return {
            node_id
            for node_id in eligible_ids
            if arm_path_by_call[node_id][: len(prefix)] == prefix
        }

    def build_container(prefix: ArmPath) -> tuple[Structure, ...]:
        direct_members = {
            node_id
            for node_id in eligible_ids
            if arm_path_by_call[node_id] == prefix
        }
        positioned: list[tuple[int, Structure]] = [
            (min(order[node_id] for node_id in run), LinearStructure(run))
            for run in _linear_runs(direct_members, outgoing, incoming, order)
        ]

        child_group_ids = {
            arm_path_by_call[node_id][len(prefix)][0]
            for node_id in eligible_ids
            if len(arm_path_by_call[node_id]) > len(prefix)
            and arm_path_by_call[node_id][: len(prefix)] == prefix
        }
        for group_id in child_group_ids:
            labels = arm_labels[(prefix, group_id)]
            ordered_labels = sorted(
                labels,
                key=lambda label: (
                    10**9
                    if not (members := below((*prefix, (group_id, label))))
                    else min(order[node_id] for node_id in members),
                    label,
                ),
            )
            arms = tuple(
                build_container((*prefix, (group_id, label)))
                for label in ordered_labels
            )
            branch_members = set().union(
                *(below((*prefix, (group_id, label))) for label in labels)
            )
            branch_order = min(
                (order[node_id] for node_id in branch_members),
                default=10**9,
            )
            positioned.append((branch_order, BranchStructure(group_id, arms)))

        return tuple(
            structure
            for _, structure in sorted(
                positioned,
                key=lambda item: (
                    item[0],
                    0 if isinstance(item[1], LinearStructure) else 1,
                ),
            )
        )

    return MethodStructure(method.entryId, build_container(()))


def build_method_structures(
    methods_by_entry_id: dict[str, MethodDefinition],
    excluded: dict[str, ExclusionReason] | None = None,
) -> dict[str, MethodStructure]:
    """Build phase-specific structure for every supplied method definition."""
    return {
        entry_id: build_method_structure(method, excluded)
        for entry_id, method in methods_by_entry_id.items()
    }
