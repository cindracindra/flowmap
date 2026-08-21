import itertools
from collections import deque

from model import Edge, Node

def _adjacency_out(edges: list[Edge]) -> dict[str, list[str]]:
    adjacency: dict[str, list[str]] = {}
    for edge in edges:
        adjacency.setdefault(edge.source, []).append(edge.target)
    return adjacency


def _reachable_non_members(
    start: str,
    own_arm: set[str],
    group_members: set[str],
    flow_out: dict[str, list[str]],
) -> set[str]:
    """
    Every node forward-reachable from `start` that belongs to NO arm of
    this group.
    """
    reached: set[str] = set()
    non_members: set[str] = set()
    stack = [start]
    while stack:
        node_id = stack.pop()
        if node_id in reached:
            continue
        reached.add(node_id)
        if node_id not in group_members:
            non_members.add(node_id)
        for target in flow_out.get(node_id, []):
            if target in group_members and target not in own_arm:
                continue
            stack.append(target)
    return non_members


def _walk_order(
    adjacency: dict[str, list[str]], starts: list[str], nodes: list[Node]
) -> dict[str, int]:
    """
    Visit index per node, BFS outward from `starts`. This is the only
    honest source of "which call comes first" -- extraction's own arm
    ordering is AST order, and the two disagree whenever a call is nested
    inside another expression (`throw new Foo(bar())` runs bar(), then the
    constructor, then the throw, so the AST-first call is the CFG-last
    one).

    The caller supplies the adjacency because the right edges differ by
    stage: a pre-flatten graph is one disjoint SEQUENCE component per
    method, walked from every entry; a flattened trace is one component
    whose flow crosses invoke edges into inlined callees, walked from the
    root. Walking a flattened graph on sequence edges alone leaves almost
    everything unreached (a call site's own sequence edge is replaced by
    the callee's return edge), and unreached nodes get arbitrary indices.
    """
    order: dict[str, int] = {}
    counter = itertools.count()
    for start in starts:
        if start in order:
            continue
        order[start] = next(counter)
        frontier: deque[str] = deque([start])
        while frontier:
            current = frontier.popleft()
            for target in adjacency.get(current, []):
                if target not in order:
                    order[target] = next(counter)
                    frontier.append(target)

    # Anything the walk never reached (an orphaned component) still needs
    # an index so callers can sort without special-casing.
    for node in nodes:
        if node.id not in order:
            order[node.id] = next(counter)
    return order
