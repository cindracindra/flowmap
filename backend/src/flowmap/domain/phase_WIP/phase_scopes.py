"""UPDATED Stage 2: the shape of each method.

A scope is one straight run of operations: no fork, no merge, one method, one
branch-arm context. It carries no information about the program -- it exists so
segmentation can assume "the previous operation" is unambiguous.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from domain.phase_exclusion import ExclusionReason
from model import Graph


ArmTags = frozenset[tuple[str, str]]


@dataclass(frozen=True, slots=True)
class Scope:
    id: str
    methodEntryId: str

    # The arm tag set defining the region. Empty means the method's top level.
    # Region nesting is set inclusion between these.
    tags: ArmTags

    # One straight run, in path order.
    nodeIds: tuple[str, ...]


def _distance_from_starts(
    calls_by_method: dict[str, set[str]],
    outgoing: dict[str, list[str]],
    incoming: dict[str, list[str]],
    rank: dict[str, int],
) -> dict[str, int]:
    """Breadth-first distance of every call from the start of its own method.

    Measured over call-to-call edges only. The entry node cannot be used as the
    seed: `full_cfg.sc` emits an entry edge to every first call it finds along
    every path, so an entry commonly fans out to several calls at different
    points in the method -- `removeItemFromCart`'s entry reaches its first
    statement, one guard's error branch and a mid-method call, all at once. The
    genuine start is the call nothing else leads to.
    """
    distance: dict[str, int] = {}
    for method_calls, in ((calls,) for calls in calls_by_method.values()):
        seeds = [
            node_id for node_id in method_calls
            if not any(source in method_calls for source in incoming[node_id])
        ]
        if not seeds:
            # Every call is in a cycle, so no start is defined by degree.
            seeds = [min(method_calls, key=lambda node_id: rank[node_id])]

        frontier = sorted(seeds, key=lambda node_id: rank[node_id])
        for node_id in frontier:
            distance[node_id] = 0
        step = 0
        while frontier:
            step += 1
            nxt: list[str] = []
            for node_id in frontier:
                for target in outgoing[node_id]:
                    if target in method_calls and target not in distance:
                        distance[target] = step
                        nxt.append(target)
            frontier = nxt
    return distance


def _plain_sequence_edges(graph: Graph) -> list[tuple[str, str]]:
    return [
        (edge.source, edge.target)
        for edge in graph.edges
        if edge.type == "sequence" and edge.returnFrom is None
    ]


def _paths(
    node_ids: set[str],
    outgoing: dict[str, list[str]],
    incoming: dict[str, list[str]],
    rank: dict[str, int],
) -> list[tuple[str, ...]]:
    """Split a region into maximal runs of uniquely-connected operations.

    Two operations stay in one run only if they are each other's unique
    neighbour in the method's real control flow, and that neighbour is in this
    region. One rule then covers forks, merges, loop back-edges and branches
    alike: a loop head has two predecessors and starts a run, a loop tail has two
    successors and ends one, and a guard forks to its arm and so ends the run
    before the code that only runs once the guard has passed.

    `outgoing` and `incoming` are method-wide, which is what makes a branch
    visible here: a guard's arm lives in another region, so degrees counted
    inside this one would see a single successor and run straight past it.
    """
    visited: set[str] = set() # loop back guard
    paths: list[tuple[str, ...]] = []

    def follows(node_id: str) -> str | None:
        """The one operation of this region that unconditionally follows."""
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
            node_id for node_id in node_ids
            if len(incoming[node_id]) != 1
            or incoming[node_id][0] not in node_ids
            or follows(incoming[node_id][0]) != node_id
        ),
        key=lambda node_id: rank[node_id],
    )
    for start in starts:
        if start not in visited:
            paths.append(consume(start))

    for node_id in sorted(node_ids - visited, key=lambda n: rank[n]):
        if node_id not in visited:
            paths.append(consume(node_id))

    return paths


def _scope_order(
    graph: Graph, distance: dict[str, int], rank: dict[str, int]
):
    """Sort key for a scope's first operation.

    Distance along control flow orders the bulk of it. Within a tie the rule is
    what the CFG shape means:

    * a **branch point** is a condition, so it runs before the arms it guards;
    * **arm content** runs before code that is only reachable past the branch --x
      untagged code tying with an arm is post-convergence, reached by an empty
      or short sibling arm.

    Still an approximation rather than the region-tree ordering: groups nested at
    different depths can interleave. That is what collapse would need.
    """
    branch_points = {
        node_id for group in graph.branchGroups for node_id in group.branchPointIds
    }
    tagged = {node.id for node in graph.nodes if node.branchArms}

    def key(node_id: str) -> tuple[int, int, int]:
        if node_id in branch_points:
            role = 0
        elif node_id in tagged:
            role = 1
        else:
            role = 2
        return (distance.get(node_id, 10**9), role, rank[node_id])

    return key


def build_scopes(
    graph: Graph, excluded: dict[str, ExclusionReason] | None = None
) -> dict[str, tuple[Scope, ...]]:
    """UPDATED Stage 2: every method's straight runs, keyed by its entry node id. 
    Excluded operations are dropped before partitioning.
    Scopes are ordered by their first operation's distance from the method
    start, with branch structure breaking ties.
    """
    excluded = excluded or {}
    rank = {node.id: index for index, node in enumerate(graph.nodes)}
    entry_by_name = {
        node.calleeFullName: node.id for node in graph.nodes if node.type == "entry"
    }

    buckets: dict[tuple[str, ArmTags], set[str]] = defaultdict(set)
    for node in graph.nodes:
        if node.type != "call" or node.id in excluded:
            continue
        entry_id = entry_by_name.get(node.callerMethod)
        if entry_id is None:
            continue
        tags = frozenset((tag.groupId, tag.armLabel) for tag in node.branchArms)
        buckets[(entry_id, tags)].add(node.id)

    edges = _plain_sequence_edges(graph)
    by_method: dict[str, list[Scope]] = defaultdict(list)

    # Method-wide adjacency between calls. A region's runs are cut using these
    # degrees, so an edge leaving the region still counts as a fork or a merge.
    calls = {node.id for node in graph.nodes if node.type == "call"}
    outgoing: dict[str, list[str]] = defaultdict(list)
    incoming: dict[str, list[str]] = defaultdict(list)
    for source, target in edges:
        if source in calls and target in calls:
            outgoing[source].append(target)
            incoming[target].append(source)
    for adjacency in (outgoing, incoming):
        for neighbours in adjacency.values():
            neighbours.sort(key=lambda node_id: rank[node_id])

    calls_by_method: dict[str, set[str]] = defaultdict(set)
    for (entry_id, _), node_ids in buckets.items():
        calls_by_method[entry_id] |= node_ids
    order = _scope_order(
        graph, _distance_from_starts(calls_by_method, outgoing, incoming, rank), rank
    )

    for (entry_id, tags), node_ids in buckets.items():

        for path in _paths(node_ids, outgoing, incoming, rank):
            by_method[entry_id].append(
                Scope(id="", methodEntryId=entry_id, tags=tags, nodeIds=path)
            )

    return {
        entry_id: tuple(
            Scope(
                id=f"scope:{entry_id}:{index}",
                methodEntryId=scope.methodEntryId,
                tags=scope.tags,
                nodeIds=scope.nodeIds,
            )
            for index, scope in enumerate(
                sorted(scopes, key=lambda s: order(s.nodeIds[0]))
            )
        )
        for entry_id, scopes in by_method.items()
    }
