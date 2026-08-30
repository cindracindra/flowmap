import dataclasses

from domain.branch_requirements import merge_branch_requirements
from domain.cfg_semantics import scoped_semantic_features
from domain.util import is_jdk_call_site_strip, is_lambda_method, is_noise
from model import (
    BranchGroup,
    BranchRequirement,
    Edge,
    Graph,
    LoopGroup,
    Node,
)


@dataclasses.dataclass(frozen=True)
class _ResolvedRoute:
    target: str
    branch_requirements: tuple[BranchRequirement, ...] = ()


def _route_key(route: _ResolvedRoute) -> tuple[object, ...]:
    return (
        route.target,
        tuple((item.groupId, item.armLabel) for item in route.branch_requirements),
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
            requirements = merge_branch_requirements(
                edge.branchRequirements, suffix.branch_requirements
            )
            if requirements is None:
                continue
            resolved.append(_ResolvedRoute(
                target=suffix.target,
                branch_requirements=tuple(requirements),
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
            requirements = merge_branch_requirements(
                edge.branchRequirements, suffix.branch_requirements
            )
            if requirements is None or edge.source == suffix.target:
                continue
            route = _ResolvedRoute(
                target=suffix.target,
                branch_requirements=tuple(requirements),
            )
            key = (edge.source, edge.type, *_route_key(route))
            if key in seen_routes:
                continue
            seen_routes.add(key)
            bridged.append(Edge(
                source=edge.source,
                target=route.target,
                type=edge.type,
                branchRequirements=list(route.branch_requirements),
            ))
    return bridged


def _drop_removed_group_requirements(
    edges: list[Edge], removed_branch_ids: set[str]
) -> list[Edge]:
    """Make transparent removed groups disappear from the route contract.

    Once a BranchGroup is removed, retaining one of its arm selections would
    leave a dangling requirement that no frontend selection can satisfy.
    Stripping such requirements can collapse formerly distinct arm routes, so
    deduplicate the complete edge identity at the same time.
    """
    if not removed_branch_ids:
        return edges

    deduplicated: dict[tuple[object, ...], Edge] = {}
    for edge in edges:
        requirements = [
            requirement
            for requirement in edge.branchRequirements
            if requirement.groupId not in removed_branch_ids
        ]
        rebuilt = dataclasses.replace(edge, branchRequirements=requirements)
        key = (
            rebuilt.source,
            rebuilt.target,
            rebuilt.type,
            tuple(
                (requirement.groupId, requirement.armLabel)
                for requirement in rebuilt.branchRequirements
            ),
        )
        deduplicated.setdefault(key, rebuilt)
    return list(deduplicated.values())


def _drop_removed_group_memberships(
    nodes: list[Node], removed_branch_ids: set[str], removed_loop_ids: set[str]
) -> list[Node]:
    """Remove every node reference to a structure that no longer exists."""
    if not removed_branch_ids and not removed_loop_ids:
        return nodes
    return [
        dataclasses.replace(
            node,
            branchArms=[
                membership
                for membership in node.branchArms
                if membership.groupId not in removed_branch_ids
            ],
            loopIds=[
                loop_id for loop_id in node.loopIds
                if loop_id not in removed_loop_ids
            ],
            targetStructureGroupId=(
                None
                if node.targetStructureGroupId in removed_loop_ids
                else node.targetStructureGroupId
            ),
        )
        for node in nodes
    ]


def _retained_structure_ids(
    nodes: list[Node],
    branch_groups: list[BranchGroup],
    loop_groups: list[LoopGroup],
) -> tuple[set[str], set[str]]:
    """Find meaningful structures to a fixed point without using geometry."""
    node_by_id = {node.id: node for node in nodes}
    child_entries = [
        (group.id, group.entryNodeId)
        for group in [*branch_groups, *loop_groups]
        if group.entryNodeId is not None
    ]

    branch_has_content: dict[str, bool] = {}
    for group in branch_groups:
        visible = any(
            node.type == "call"
            and any(ref.groupId == group.id for ref in node.branchArms)
            for node in nodes
        )
        terminal = any(
            (node.type == "exit" and node.exitKind in {"return", "throw"})
            or node.type == "transfer"
            for node in nodes
            if any(ref.groupId == group.id for ref in node.branchArms)
        )
        outcomes = {
            frozenset(
                (exit_.kind, exit_.destinationNodeId)
                for exit_ in arm.exits
            )
            for arm in group.arms
        }
        branch_has_content[group.id] = visible or terminal or len(outcomes) > 1

    loop_has_content = {
        group.id: any(
            node.type == "call" and group.id in node.loopIds
            for node in nodes
        ) or any(
            node.type == "transfer"
            and (
                node.targetStructureGroupId == group.id
                or group.id in node.loopIds
            )
            for node in nodes
        )
        for group in loop_groups
    }

    retained_branches = {
        group_id for group_id, meaningful in branch_has_content.items()
        if meaningful
    }
    retained_loops = {
        group_id for group_id, meaningful in loop_has_content.items()
        if meaningful
    }
    while True:
        next_branches = {
            group.id
            for group in branch_groups
            if branch_has_content[group.id]
            or any(
                child_id != group.id
                and (
                    child_id in retained_branches or child_id in retained_loops
                )
                and (
                    (entry := node_by_id.get(entry_id)) is not None
                    and any(
                        ref.groupId == group.id for ref in entry.branchArms
                    )
                )
                for child_id, entry_id in child_entries
            )
        }
        next_loops = {
            group.id
            for group in loop_groups
            if loop_has_content[group.id]
            or any(
                child_id != group.id
                and (
                    child_id in retained_branches or child_id in retained_loops
                )
                and (
                    (entry := node_by_id.get(entry_id)) is not None
                    and group.id in entry.loopIds
                )
                for child_id, entry_id in child_entries
            )
        }
        if next_branches == retained_branches and next_loops == retained_loops:
            return next_branches, next_loops
        retained_branches, retained_loops = next_branches, next_loops


def _refresh_branch_metadata(
    groups: list[BranchGroup], nodes: list[Node], retained_node_ids: set[str]
) -> list[BranchGroup]:
    """Refresh visibility and retained ArmExit destinations after filtering."""
    rebuilt: list[BranchGroup] = []
    for group in groups:
        arms = []
        for arm in group.arms:
            has_visible_call = any(
                node.type == "call"
                and any(
                    ref.groupId == group.id and ref.armLabel == arm.label
                    for ref in node.branchArms
                )
                for node in nodes
            )
            arms.append(dataclasses.replace(
                arm,
                empty=not has_visible_call,
                exits=[
                    dataclasses.replace(
                        exit_,
                        destinationNodeId=(
                            exit_.destinationNodeId
                            if exit_.destinationNodeId in retained_node_ids
                            else None
                        ),
                    )
                    for exit_ in arm.exits
                ],
            ))
        rebuilt.append(dataclasses.replace(
            group,
            arms=arms,
            enclosingRequirements=[
                requirement
                for requirement in group.enclosingRequirements
                if any(candidate.id == requirement.groupId for candidate in groups)
            ],
        ))
    return rebuilt


def filter_noise_cfg(cfg: Graph, *, preserve_all_entries: bool = False) -> Graph:
    """
    Drops noise/JDK-bookkeeping "call" nodes, bridging around each gap so
    the surrounding flow stays connected (e.g. A -> B -> C with B
    excluded becomes A -> C). Also tags each surviving node whose
    It also removes structurally empty branch and loop groups to a fixed point.
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

    retained_branch_ids, retained_loop_ids = _retained_structure_ids(
        kept_nodes, cfg.branchGroups, cfg.loopGroups
    )
    removed_branch_ids = {
        group.id for group in cfg.branchGroups
    } - retained_branch_ids
    removed_loop_ids = {
        group.id for group in cfg.loopGroups
    } - retained_loop_ids

    # Scope metadata must disappear before transparent anchors are bridged.
    # Otherwise alternative empty arms can combine as an impossible AND.
    kept_edges = _drop_removed_group_requirements(
        kept_edges, removed_branch_ids
    )
    kept_nodes = _drop_removed_group_memberships(
        kept_nodes, removed_branch_ids, removed_loop_ids
    )
    removed_structure_ids = {
        node.id
        for node in kept_nodes
        if (
            node.type == "structure"
            and node.structureGroupId in (
                removed_branch_ids | removed_loop_ids
            )
        )
    }
    if removed_structure_ids:
        kept_nodes = [
            node for node in kept_nodes
            if node.id not in removed_structure_ids
        ]
        rebridged_edges: list[Edge] = []
        for edge_type in sorted({edge.type for edge in kept_edges}):
            rebridged_edges.extend(_bridge_edges(
                [edge for edge in kept_edges if edge.type == edge_type],
                removed_structure_ids,
            ))
        kept_edges = rebridged_edges

    groups = _refresh_branch_metadata(
        [
            group for group in cfg.branchGroups
            if group.id in retained_branch_ids
        ],
        kept_nodes,
        {node.id for node in kept_nodes},
    )
    loops = [
        group for group in cfg.loopGroups if group.id in retained_loop_ids
    ]
    return dataclasses.replace(
        cfg,
        nodes=kept_nodes,
        edges=kept_edges,
        branchGroups=groups,
        loopGroups=loops,
        semanticFeatures=scoped_semantic_features(
            cfg.semanticFeatures, {node.id for node in kept_nodes}
        ),
    )
