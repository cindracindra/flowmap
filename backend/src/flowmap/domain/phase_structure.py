"""Stage 2: derive each method's nested structure from the filtered CFG."""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from typing import TypeAlias

from domain.phase_exclusion import ExclusionReason
from domain.method_scoping import build_method_definitions
from model import Graph, MethodDefinition


ArmPath = tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class LinearStructure:
    """One genuine execution-ordered run with no fork or merge."""

    nodeIds: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class BranchStructure:
    """One branch whose tuple entries are its alternative arms."""

    groupId: str
    arms: tuple[tuple[Structure, ...], ...]


Structure: TypeAlias = LinearStructure | BranchStructure


@dataclass(frozen=True, slots=True)
class MethodStructure:
    methodEntryId: str
    structures: tuple[Structure, ...]


def _paths(
    node_ids: set[str],
    outgoing: dict[str, list[str]],
    incoming: dict[str, list[str]],
    order: dict[str, int],
) -> list[tuple[str, ...]]:
    """Split nodes into maximal uniquely-connected linear runs."""
    visited: set[str] = set()

    def follows(node_id: str) -> str | None:
        successors = outgoing[node_id]
        if len(successors) != 1:
            return None
        successor = successors[0]
        if successor not in node_ids or len(incoming[successor]) != 1:
            return None
        return successor

    def consume(start: str) -> tuple[str, ...]:
        path: list[str] = []
        current: str | None = start
        while current is not None and current not in visited:
            visited.add(current)
            path.append(current)
            current = follows(current)
        return tuple(path)

    starts = sorted(
        (
            node_id
            for node_id in node_ids
            if len(incoming[node_id]) != 1
            or incoming[node_id][0] not in node_ids
            or follows(incoming[node_id][0]) != node_id
        ),
        key=lambda node_id: order[node_id],
    )
    paths = [consume(start) for start in starts if start not in visited]

    # A closed cycle has no degree-defined start.
    for node_id in sorted(node_ids - visited, key=lambda value: order[value]):
        if node_id not in visited:
            paths.append(consume(node_id))
    return paths


def _execution_order(
    method_nodes: set[str],
    outgoing: dict[str, list[str]],
    incoming: dict[str, list[str]],
    rank: dict[str, int],
) -> dict[str, int]:
    """Return deterministic method-local breadth-first execution order."""
    starts = sorted(
        (
            node_id
            for node_id in method_nodes
            if not any(source in method_nodes for source in incoming[node_id])
        ),
        key=lambda node_id: rank[node_id],
    )
    if not starts and method_nodes:
        starts = [min(method_nodes, key=lambda node_id: rank[node_id])]

    order: dict[str, int] = {}
    queue = deque(starts)
    while queue:
        node_id = queue.popleft()
        if node_id in order:
            continue
        order[node_id] = len(order)
        queue.extend(
            sorted(
                (
                    target
                    for target in outgoing[node_id]
                    if target in method_nodes and target not in order
                ),
                key=lambda target: rank[target],
            )
        )

    for node_id in sorted(method_nodes - order.keys(), key=lambda value: rank[value]):
        order[node_id] = len(order)
    return order


def build_method_structures(
    graph: Graph,
    excluded: dict[str, ExclusionReason] | None = None,
    method_definitions: dict[str, MethodDefinition] | None = None,
) -> dict[str, MethodStructure]:
    """Build a nested structure tree for every method containing calls.

    Branch membership and nesting come only from each node's ordered
    ``branchArms``. Excluded nodes are omitted from structures, while their
    edges and tags still preserve the shape and ordering of the filtered CFG.
    """
    excluded = excluded or {}
    method_definitions = method_definitions or build_method_definitions(graph)
    rank = {node.id: index for index, node in enumerate(graph.nodes)}

    calls_by_entry: dict[str, set[str]] = defaultdict(set)
    paths_by_node: dict[str, ArmPath] = {}
    arm_labels: dict[tuple[str, ArmPath, str], set[str]] = defaultdict(set)

    for entry_id, method in method_definitions.items():
        for node in method.nodes:
            if node.type != "call":
                continue
            calls_by_entry[entry_id].add(node.id)
            arm_path = tuple((tag.groupId, tag.armLabel) for tag in node.branchArms)
            paths_by_node[node.id] = arm_path
            prefix: ArmPath = ()
            for group_id, arm_label in arm_path:
                arm_labels[(entry_id, prefix, group_id)].add(arm_label)
                prefix = (*prefix, (group_id, arm_label))

    outgoing: dict[str, list[str]] = defaultdict(list)
    incoming: dict[str, list[str]] = defaultdict(list)
    call_ids = set(paths_by_node)
    for method in method_definitions.values():
        for edge in method.sequenceEdges:
            source, target = edge.source, edge.target
            if (
                edge.returnFrom is None
                and source in call_ids
                and target in call_ids
            ):
                outgoing[source].append(target)
                incoming[target].append(source)
    for adjacency in (outgoing, incoming):
        for neighbours in adjacency.values():
            neighbours.sort(key=lambda node_id: rank[node_id])

    result: dict[str, MethodStructure] = {}
    for entry_id, all_method_nodes in calls_by_entry.items():
        order = _execution_order(all_method_nodes, outgoing, incoming, rank)
        eligible = all_method_nodes - excluded.keys()

        def members_below(prefix: ArmPath) -> set[str]:
            return {
                node_id
                for node_id in eligible
                if paths_by_node[node_id][: len(prefix)] == prefix
            }

        def build_container(prefix: ArmPath) -> tuple[Structure, ...]:
            direct = {
                node_id for node_id in eligible if paths_by_node[node_id] == prefix
            }
            items: list[tuple[int, Structure]] = [
                (min(order[node_id] for node_id in path), LinearStructure(path))
                for path in _paths(direct, outgoing, incoming, order)
            ]

            child_groups = {
                paths_by_node[node_id][len(prefix)][0]
                for node_id in eligible
                if len(paths_by_node[node_id]) > len(prefix)
                and paths_by_node[node_id][: len(prefix)] == prefix
            }
            for group_id in child_groups:
                labels = arm_labels[(entry_id, prefix, group_id)]
                ordered_labels = sorted(
                    labels,
                    key=lambda label: (
                        min(
                            (
                                order[node_id]
                                for node_id in members_below((*prefix, (group_id, label)))
                            ),
                            default=10**9,
                        ),
                        label,
                    ),
                )
                arms = tuple(
                    build_container((*prefix, (group_id, label)))
                    for label in ordered_labels
                )
                branch_members = {
                    node_id
                    for label in labels
                    for node_id in members_below((*prefix, (group_id, label)))
                }
                branch_order = min(
                    (order[node_id] for node_id in branch_members),
                    default=10**9,
                )
                items.append((branch_order, BranchStructure(group_id, arms)))

            return tuple(
                structure
                for _, structure in sorted(
                    items,
                    key=lambda item: (
                        item[0],
                        0 if isinstance(item[1], LinearStructure) else 1,
                    ),
                )
            )

        result[entry_id] = MethodStructure(entry_id, build_container(()))

    return result
