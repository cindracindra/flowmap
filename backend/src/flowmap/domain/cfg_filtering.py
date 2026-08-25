import dataclasses
from collections.abc import Iterable

from domain.cfg_branching import _recompute_branch_geometry
from domain.cfg_semantics import scoped_semantic_features
from domain.util import is_jdk_call_site_strip, is_lambda_method, is_noise
from model import ArmExit, BranchArm, BranchGroup, BranchRequirement, Edge, Graph, Node


def _tag_dead_ends(nodes: list[Node]) -> list[Node]:
    """Mark calls whose extracted terminus proves that they throw."""
    return [
        dataclasses.replace(node, deadEnd=True)
        if node.terminus == "throw"
        else node
        for node in nodes
    ]


@dataclasses.dataclass(frozen=True)
class _ResolvedRoute:
    target: str
    branch_requirements: tuple[BranchRequirement, ...] = ()
    return_from: str | None = None
    fallback: bool = False
    loop_back: bool = False


def _merge_route_requirements(
    *requirement_lists: Iterable[BranchRequirement],
) -> tuple[BranchRequirement, ...] | None:
    """Union route guards, rejecting a path that requires two arms of one group."""
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
    return tuple(merged)


def _route_key(route: _ResolvedRoute) -> tuple[object, ...]:
    return (
        route.target,
        tuple((item.groupId, item.armLabel) for item in route.branch_requirements),
        route.return_from,
        route.fallback,
        route.loop_back,
    )


def _resolve_kept_routes(
    node_id: str,
    excluded_ids: set[str],
    adjacency_out: dict[str, list[Edge]],
    memo: dict[str, list[_ResolvedRoute]],
    visiting: frozenset[str],
) -> tuple[list[_ResolvedRoute], bool]:
    """
    Follows outgoing edges until reaching kept node(s) -- or nothing, if
    the chain dead-ends entirely inside excluded territory.
    """

    if node_id not in excluded_ids:
        return [_ResolvedRoute(target=node_id)], False
    if node_id in memo:
        return memo[node_id], False
    if node_id in visiting:
        return [], True

    resolved: list[_ResolvedRoute] = []
    truncated = False
    for edge in adjacency_out.get(node_id, []):
        sub_resolved, sub_truncated = _resolve_kept_routes(
            edge.target, excluded_ids, adjacency_out, memo, visiting | {node_id}
        )
        for suffix in sub_resolved:
            requirements = _merge_route_requirements(
                edge.branchRequirements, suffix.branch_requirements
            )
            if requirements is None:
                continue
            resolved.append(_ResolvedRoute(
                target=suffix.target,
                branch_requirements=requirements,
                return_from=edge.returnFrom or suffix.return_from,
                fallback=edge.fallback or suffix.fallback,
                loop_back=edge.loopBack or suffix.loop_back,
            ))
        truncated = truncated or sub_truncated
    resolved = list({_route_key(route): route for route in resolved}.values())
    if not truncated:
        memo[node_id] = resolved
    return resolved, truncated


def _bridge_edges(typed_edges: list[Edge], excluded_ids: set[str]) -> list[Edge]:
    """
    Rebuilds edges with excluded nodes spliced out: for every surviving
    edge whose source is a kept node, its target is resolved to the nearest
    kept descendant(s).
    """
    adjacency_out: dict[str, list[Edge]] = {}
    for edge in typed_edges:
        adjacency_out.setdefault(edge.source, []).append(edge)

    memo: dict[str, list[_ResolvedRoute]] = {}
    seen_routes: set[tuple[object, ...]] = set()
    bridged: list[Edge] = []
    for edge in typed_edges:
        if edge.source in excluded_ids:
            continue
        suffixes, _ = _resolve_kept_routes(
            edge.target, excluded_ids, adjacency_out, memo, frozenset()
        )
        for suffix in suffixes:
            requirements = _merge_route_requirements(
                edge.branchRequirements, suffix.branch_requirements
            )
            if requirements is None or edge.source == suffix.target:
                continue
            route = _ResolvedRoute(
                target=suffix.target,
                branch_requirements=requirements,
                return_from=edge.returnFrom or suffix.return_from,
                fallback=edge.fallback or suffix.fallback,
                loop_back=edge.loopBack or suffix.loop_back,
            )
            key = (edge.source, edge.type, *_route_key(route))
            if key in seen_routes:
                continue
            seen_routes.add(key)
            bridged.append(Edge(
                source=edge.source,
                target=route.target,
                type=edge.type,
                returnFrom=route.return_from,
                fallback=route.fallback,
                loopBack=route.loop_back,
                branchRequirements=list(route.branch_requirements),
            ))
    return bridged


def _annotate_filtered_method_routes(graph: Graph) -> Graph:
    """Put the executable branch contract on the filtered graph itself."""
    # Imports stay local to keep cfg filtering independent during module startup.
    from domain.method_branch_routing import prepare_all_method_branch_routes
    from domain.method_scoping import build_method_definitions

    methods = prepare_all_method_branch_routes(build_method_definitions(graph))
    if not methods:
        return graph

    owned_sequence_keys: set[tuple[str, str]] = set()
    routed_edges: list[Edge] = []
    routed_groups: list[BranchGroup] = []
    routed_group_ids: set[str] = set()
    for method in methods.values():
        member_ids = {method.entryId, *(node.id for node in method.nodes)}
        owned_sequence_keys.update(
            (edge.source, edge.target)
            for edge in graph.edges
            if edge.type == "sequence"
            and edge.source in member_ids
            and edge.target in member_ids
        )
        routed_edges.extend(method.sequenceEdges)
        routed_groups.extend(method.branchGroups)
        routed_group_ids.update(group.id for group in method.branchGroups)

    retained_edges = [
        edge for edge in graph.edges
        if edge.type != "sequence"
        or (edge.source, edge.target) not in owned_sequence_keys
    ]
    retained_groups = [
        group for group in graph.branchGroups if group.id not in routed_group_ids
    ]
    return dataclasses.replace(
        graph,
        edges=[*retained_edges, *routed_edges],
        branchGroups=[*retained_groups, *routed_groups],
    )


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

    filtered = dataclasses.replace(
        cfg,
        nodes=kept_nodes,
        edges=kept_edges,
        branchGroups=groups,
        semanticFeatures=scoped_semantic_features(
            cfg.semanticFeatures, {node.id for node in kept_nodes}, kept_edges
        ),
    )
    return _annotate_filtered_method_routes(filtered)
