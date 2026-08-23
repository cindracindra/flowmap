import dataclasses

from domain.cfg_branching import _recompute_branch_geometry
from domain.cfg_semantics import scoped_semantic_features
from domain.cfg_traversal import _adjacency_out
from domain.util import is_jdk_call_site_strip, is_lambda_method, is_noise
from model import Edge, Graph, Node


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
        return [], True  # cycle entirely within excluded nodes -- nothing to bridge to

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
    edge whose source is a kept node, its target is resolved to the
    nearest kept descendant(s).
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
            if pair in seen_pairs or pair[0] == pair[1]:
                continue
            seen_pairs.add(pair)
            bridged.append(Edge(source=pair[0], target=pair[1], type=edge.type))
    return bridged


def filter_noise_cfg(cfg: Graph, *, preserve_all_entries: bool = False) -> Graph:
    """
    Drops noise/JDK-bookkeeping "call" nodes, bridging around each gap so
    the surrounding flow stays connected (e.g. A -> B -> C with B
    excluded becomes A -> C). Also tags each surviving node whose
    terminus is a proven throw with deadEnd=True, and re-derives each
    branch group against what survived (_recompute_branch_geometry).

    A node's own branchArms tags are never moved: full_cfg.sc tags every
    call in an arm, so anything genuinely inside one is already tagged,
    and the only place a migrated tag could land is outside the arm.
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
        # A normally-noisy API call can be the only execution bridge to an
        # internal lambda implementation. Removing that call
        # also removes its invoke edge and exposes the implementation as a
        # false root, so retain such bridge calls.
        and n.id not in internal_invoke_sources
        and n.calleeFullName is not None
        and (is_noise(n.calleeFullName) or is_jdk_call_site_strip(n.calleeFullName))
    }
    if not excluded_ids:
        return dataclasses.replace(
            cfg,
            nodes=_tag_dead_ends(cfg.nodes),
            branchGroups=_recompute_branch_geometry(
                cfg.nodes, cfg.edges, cfg.branchGroups
            ),
        )

    throw_ids = {
        n.id
        for n in cfg.nodes
        if n.type == "call" and n.calleeFullName == "<operator>.throw"
    }

    # throw_ids is still needed here, independent of the deadEnd flag
    # above: it stops bridging from treating whatever lexically follows a
    # `throw` (Joern's cfgNext doesn't model exception control flow, DESIGN.md
    # #4.3) as reachable from the call that threw. That's an edge-topology
    # concern, not a flag-semantics one -- unaffected by how deadEnd itself
    # gets computed.
    kept_nodes = _tag_dead_ends([n for n in cfg.nodes if n.id not in excluded_ids])

    edge_types = sorted({e.type for e in cfg.edges})
    kept_edges: list[Edge] = []
    for edge_type in edge_types:
        typed_edges = [e for e in cfg.edges if e.type == edge_type]
        if edge_type == "sequence":
            typed_edges = [e for e in typed_edges if e.source not in throw_ids]
        kept_edges.extend(_bridge_edges(typed_edges, excluded_ids))

    # A node excluded nowhere above can still end up disconnected: a
    # "leaf" whose only edge was FROM an excluded call has no surviving
    # edge at all once that call's edges are dropped (not bridged --
    # bridging only carries a kept SOURCE's edge past excluded nodes; an
    # edge whose source is itself excluded is just gone). Not incorrect
    # downstream (nothing iterates `nodes` directly, only walks edges
    # from the root), but it's dead clutter in the output -- pruned here
    # rather than left for a human staring at filtered_cfg.json to
    # puzzle over.
    #
    # Roots are exempt, and there can be more than one: a single-root
    # slice_from_root output has exactly cfg.entryPoint; the whole
    # multi-root graph (cfg.entryPoint is None) has cfg.roots instead --
    # e.g. a root whose only content is a since-filtered call (confirmed
    # live: main()'s sole println gets stripped as JDK noise) would
    # otherwise be pruned right along with genuine dead weight, same as
    # any other disconnected node, since it's a legitimate zero-edge
    # entry either way and needs to keep resolving to SOME node.
    connected_ids = {e.source for e in kept_edges} | {e.target for e in kept_edges}
    root_ids = {
        n.id
        for n in kept_nodes
        if n.type == "entry" and n.calleeFullName == cfg.entryPoint
    }
    root_ids |= set(cfg.roots)
    if preserve_all_entries:
        root_ids |= {n.id for n in kept_nodes if n.type == "entry"}
    kept_nodes = [n for n in kept_nodes if n.id in connected_ids or n.id in root_ids]

    return dataclasses.replace(
        cfg,
        nodes=kept_nodes,
        edges=kept_edges,
        branchGroups=_recompute_branch_geometry(
            kept_nodes, kept_edges, cfg.branchGroups
        ),
        semanticFeatures=scoped_semantic_features(
            cfg.semanticFeatures, {node.id for node in kept_nodes}, kept_edges
        ),
    )
