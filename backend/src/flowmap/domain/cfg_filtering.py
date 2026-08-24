import dataclasses

from domain.cfg_branching import _recompute_branch_geometry
from domain.cfg_semantics import scoped_semantic_features
from domain.cfg_traversal import _adjacency_out
from domain.util import is_jdk_call_site_strip, is_lambda_method, is_noise
from model import ArmExit, BranchArm, BranchGroup, Edge, Graph, Node


def _tag_dead_ends(nodes: list[Node]) -> list[Node]:
    """Mark calls whose extracted terminus proves that they throw."""
    return [
        dataclasses.replace(node, deadEnd=True)
        if node.terminus == "throw"
        else node
        for node in nodes
    ]


def _resolve_kept_targets(
    node_id: str,
    excluded_ids: set[str],
    adjacency_out: dict[str, list[str]],
    memo: dict[str, list[str]],
    visiting: frozenset[str],
) -> tuple[list[str], bool]:
    """
    Follows outgoing edges until reaching kept node(s) -- or nothing, if
    the chain dead-ends entirely inside excluded territory.
    """

    if node_id not in excluded_ids:
        return [node_id], False
    if node_id in memo:
        return memo[node_id], False
    if node_id in visiting:
        return [], True

    resolved: list[str] = []
    truncated = False
    for successor in adjacency_out.get(node_id, []):
        sub_resolved, sub_truncated = _resolve_kept_targets(
            successor, excluded_ids, adjacency_out, memo, visiting | {node_id}
        )
        resolved.extend(sub_resolved)
        truncated = truncated or sub_truncated
    if not truncated:
        memo[node_id] = resolved
    return resolved, truncated


def _bridge_edges(typed_edges: list[Edge], excluded_ids: set[str]) -> list[Edge]:
    """
    Rebuilds edges with excluded nodes spliced out: for every surviving
    edge whose source is a kept node, its target is resolved to the nearest
    kept descendant(s).
    """
    adjacency_out = _adjacency_out(typed_edges)

    memo: dict[str, list[str]] = {}
    seen_pairs: set[tuple[str, str]] = set()
    bridged: list[Edge] = []
    for edge in typed_edges:
        if edge.source in excluded_ids:
            continue
        targets, _ = _resolve_kept_targets(
            edge.target, excluded_ids, adjacency_out, memo, frozenset()
        )
        for target in targets:
            pair = (edge.source, target)
            if pair in seen_pairs or pair[0] == pair[1]: # e.g. case A → excludedNode → A
                continue
            seen_pairs.add(pair)
            bridged.append(Edge(source=pair[0], target=pair[1], type=edge.type))
    return bridged


def _nearest_surviving_frontiers(
    node_id: str,
    kept_ids: set[str],
    sequence_in: dict[str, list[str]],
    nodes_by_id: dict[str, Node],
    visiting: frozenset[str] = frozenset(),
) -> list[str]:
    """Resolve an extraction-time frontier to nearest retained active nodes.
    Frontier resolution is intentionally backwards.
    """
    if node_id in visiting:
        return []
    node = nodes_by_id.get(node_id)
    if node_id in kept_ids and node is not None and node.type in ("call", "entry"):
        return [node_id]

    resolved: list[str] = []
    for predecessor in sequence_in.get(node_id, []):
        resolved.extend(_nearest_surviving_frontiers(
            predecessor,
            kept_ids,
            sequence_in,
            nodes_by_id,
            visiting | {node_id},
        ))
    return list(dict.fromkeys(resolved))


def _recompute_arm_exit_frontiers(
    groups: list[BranchGroup],
    original_nodes: list[Node],
    original_edges: list[Edge],
    kept_nodes: list[Node],
) -> list[BranchGroup]:
    """Update ArmExit frontiers to the calls/entries surviving filtering."""
    nodes_by_id = {node.id: node for node in original_nodes}
    kept_ids = {node.id for node in kept_nodes}
    sequence_in: dict[str, list[str]] = {}
    for edge in original_edges:
        if edge.type == "sequence":
            sequence_in.setdefault(edge.target, []).append(edge.source)

    rebuilt: list[BranchGroup] = []
    for group in groups:
        arms: list[BranchArm] = []
        for arm in group.arms:
            exits: list[ArmExit] = []
            for exit_ in arm.exits:
                frontiers: list[str] = []
                for frontier_id in exit_.frontierIds:
                    frontiers.extend(_nearest_surviving_frontiers(
                        frontier_id, kept_ids, sequence_in, nodes_by_id
                    ))
                if not exit_.frontierIds: # A zero-call route has no active frontier, use branchPointIds
                    frontiers.extend(
                        point for point in group.branchPointIds if point in kept_ids
                    )
                exits.append(dataclasses.replace(
                    exit_, frontierIds=list(dict.fromkeys(frontiers))
                ))
            arms.append(dataclasses.replace(arm, exits=exits))
        rebuilt.append(dataclasses.replace(group, arms=arms))
    return rebuilt


def filter_noise_cfg(cfg: Graph, *, preserve_all_entries: bool = False) -> Graph:
    """
    Drops noise/JDK-bookkeeping "call" nodes, bridging around each gap so
    the surrounding flow stays connected (e.g. A -> B -> C with B
    excluded becomes A -> C). Also tags each surviving node whose
    terminus is a proven throw with deadEnd=True, and re-derives each
    branch group against what survived (_recompute_branch_geometry).
    """
    nodes_by_id = {node.id: node for node in cfg.nodes}
    internal_invoke_sources = {
        edge.source
        for edge in cfg.edges
        if edge.type == "invoke"
        and (target := nodes_by_id.get(edge.target)) is not None
        and target.type == "entry"
        and target.calleeFullName is not None
        and is_lambda_method(target.calleeFullName)
    }
    excluded_ids = {
        n.id
        for n in cfg.nodes
        if n.type == "call"
        and n.id not in internal_invoke_sources
        and n.calleeFullName is not None
        and (is_noise(n.calleeFullName) or is_jdk_call_site_strip(n.calleeFullName))
    }
    throw_ids = {
        n.id
        for n in cfg.nodes
        if n.type == "call" and n.calleeFullName == "<operator>.throw"
    }

    def valid_edge(edge: Edge) -> bool:
        """
        Reject sequence edges sequence edges sourced from <operator>.throw
        except edge to exit node.
        """
        if edge.type != "sequence" or edge.source not in throw_ids:
            return True
        target = nodes_by_id.get(edge.target)
        return (
            target is not None
            and target.type == "exit"
            and target.exitKind == "throw"
        )

    eligible_edges = [edge for edge in cfg.edges if valid_edge(edge)]

    if excluded_ids:
        kept_nodes = [node for node in cfg.nodes if node.id not in excluded_ids]
        kept_edges: list[Edge] = []
        for edge_type in sorted({edge.type for edge in eligible_edges}):
            kept_edges.extend(_bridge_edges(
                [edge for edge in eligible_edges if edge.type == edge_type],
                excluded_ids,
            ))
    else:
        kept_nodes = list(cfg.nodes)
        kept_edges = eligible_edges

    # Declutter orphaned nodes due to sole caller is from an excluded 
    # call or entire body contains only excluded calls. Roots are exempt. 
    if excluded_ids:
        connected_ids = {e.source for e in kept_edges} | {e.target for e in kept_edges}
        root_ids = {
            n.id
            for n in kept_nodes
            if n.type == "entry" and n.calleeFullName == cfg.entryPoint
        }
        root_ids |= set(cfg.roots)
        if preserve_all_entries:
            root_ids |= {n.id for n in kept_nodes if n.type == "entry"}
        kept_nodes = [
            node for node in kept_nodes
            if node.id in connected_ids or node.id in root_ids
        ]

    kept_nodes = _tag_dead_ends(kept_nodes)

    groups = _recompute_branch_geometry(kept_nodes, kept_edges, cfg.branchGroups)
    groups = _recompute_arm_exit_frontiers(
        groups, cfg.nodes, cfg.edges, kept_nodes
    )

    return dataclasses.replace(
        cfg,
        nodes=kept_nodes,
        edges=kept_edges,
        branchGroups=groups,
        semanticFeatures=scoped_semantic_features(
            cfg.semanticFeatures, {node.id for node in kept_nodes}, kept_edges
        ),
    )
