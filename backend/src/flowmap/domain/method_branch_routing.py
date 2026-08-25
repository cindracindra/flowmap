from __future__ import annotations

import dataclasses

from model import (
    BranchArm,
    BranchGroup,
    BranchRequirement,
    Edge,
    MethodDefinition,
    Node,
    arm_exit_kinds,
)


def _ordered_node_ids(method: MethodDefinition) -> dict[str, int]:
    """Stable method-local execution order, with cycles visited once."""
    outgoing: dict[str, list[str]] = {}
    for edge in method.sequenceEdges:
        outgoing.setdefault(edge.source, []).append(edge.target)

    order: dict[str, int] = {}
    pending = [method.entryId]
    while pending:
        node_id = pending.pop()
        if node_id in order:
            continue
        order[node_id] = len(order)
        pending.extend(reversed(outgoing.get(node_id, ())))
    for node_id in sorted(node.id for node in method.nodes):
        order.setdefault(node_id, len(order))
    return order


def _members_by_arm(method: MethodDefinition) -> dict[tuple[str, str], set[str]]:
    members: dict[tuple[str, str], set[str]] = {}
    for node in method.nodes:
        for ref in node.branchArms:
            members.setdefault((ref.groupId, ref.armLabel), set()).add(node.id)
    return members


def recompute_method_branch_geometry(method: MethodDefinition) -> MethodDefinition:
    """Recompute arm heads and branch points inside one method definition."""
    order = _ordered_node_ids(method)
    members = _members_by_arm(method)
    nodes = {method.entry.id: method.entry, **{node.id: node for node in method.nodes}}
    incoming: dict[str, list[str]] = {}
    outgoing: dict[str, list[str]] = {}
    for edge in method.sequenceEdges:
        incoming.setdefault(edge.target, []).append(edge.source)
        outgoing.setdefault(edge.source, []).append(edge.target)

    groups: list[BranchGroup] = []
    for group in method.branchGroups:
        arms: list[BranchArm] = []
        candidates: set[str] = set()
        group_members = set().union(*(
            members.get((group.id, arm.label), set()) for arm in group.arms
        )) if group.arms else set()
        for arm in group.arms:
            route_members = members.get((group.id, arm.label), set())
            arm_members = {
                node_id
                for node_id in route_members
                if nodes[node_id].type == "call"
            }
            head = min(arm_members, key=lambda node_id: order[node_id]) if arm_members else None
            rebuilt = dataclasses.replace(arm, firstCallId=head, empty=not arm_members)
            arms.append(rebuilt)
            if head is not None:
                candidates.update(
                    predecessor
                    for predecessor in incoming.get(head, ())
                    if predecessor not in group_members
                )
            # A return-only or throw-only arm has no surviving call head, but
            # its exit node still identifies the predecessor where the branch
            # forks. This is the common guard shape `if (x) return;`.
            for route_member in route_members:
                candidates.update(
                    predecessor
                    for predecessor in incoming.get(route_member, ())
                    if predecessor not in group_members
                )

        # Existing edge guards are a second authoritative signal, particularly
        # when an empty arm's exit node was omitted from a compatibility input.
        candidates.update(
            edge.source
            for edge in method.sequenceEdges
            if edge.source not in group_members
            and any(requirement.groupId == group.id for requirement in edge.branchRequirements)
        )

        forking = {
            candidate
            for candidate in candidates
            if len(set(outgoing.get(candidate, ()))) > 1
        }
        if forking:
            branch_points = sorted(forking, key=lambda node_id: order[node_id])
        elif candidates:
            branch_points = [max(candidates, key=lambda node_id: order[node_id])]
        else:
            branch_points = [
                node_id for node_id in group.branchPointIds if node_id in order
            ]
        groups.append(dataclasses.replace(
            group,
            arms=arms,
            branchPointIds=branch_points,
        ))
    return dataclasses.replace(method, branchGroups=groups)


def _requirements_for_node(node: Node | None) -> list[BranchRequirement]:
    if node is None:
        return []
    return [BranchRequirement(ref.groupId, ref.armLabel) for ref in node.branchArms]


def _merge_requirements(
    *requirement_lists: list[BranchRequirement],
) -> list[BranchRequirement]:
    merged: list[BranchRequirement] = []
    for requirements in requirement_lists:
        for requirement in requirements:
            if requirement not in merged:
                merged.append(requirement)
    return merged


def annotate_method_branch_requirements(method: MethodDefinition) -> MethodDefinition:
    """Tag existing local sequence routes with definition-scoped arm choices."""
    nodes = {method.entry.id: method.entry, **{node.id: node for node in method.nodes}}
    groups = {group.id: group for group in method.branchGroups}
    group_order = {group.id: index for index, group in enumerate(method.branchGroups)}
    arms = {
        (group.id, arm.label): arm
        for group in method.branchGroups
        for arm in group.arms
    }
    arms_by_group = {
        group.id: {arm.label for arm in group.arms}
        for group in method.branchGroups
    }

    edges: list[Edge] = []
    for edge in method.sequenceEdges:
        requirements = _merge_requirements(
            list(edge.branchRequirements),
            _requirements_for_node(nodes.get(edge.target)),
        )
        for group_id, labels in arms_by_group.items():
            matching = [
                requirement
                for requirement in requirements
                if requirement.groupId == group_id and requirement.armLabel in labels
            ]
            if len(matching) > 1:
                raise ValueError(
                    f"Method {method.entryId!r} edge {edge.source!r}->{edge.target!r} "
                    f"requires conflicting arms of branch {group_id!r}"
                )

        # A zero-call arm has no tagged target node. Identify its existing
        # direct route by the branch point and the group's resolved target.
        for group in groups.values():
            if edge.source not in group.branchPointIds:
                continue
            explicit_label = next(
                (
                    arm.label for arm in group.arms
                    if arm.firstCallId == edge.target
                ),
                None,
            )
            if explicit_label is not None:
                requirements = _merge_requirements(
                    requirements,
                    [BranchRequirement(group.id, explicit_label)],
                )
                continue
            empty_matches = [
                arm for arm in group.arms
                if arm.empty
                and arm_exit_kinds(arm) == {"continues"}
                and edge.target in (arm.targetIds or ())
            ]
            if len(empty_matches) == 1:
                requirements = _merge_requirements(
                    requirements,
                    [BranchRequirement(group.id, empty_matches[0].label)],
                )

        # Extraction membership describes lexical containment and can include
        # the normal arm of a later sibling guard. Execution cannot reach that
        # later choice after an earlier arm has returned or thrown, so cut the
        # route contract at its first terminal selected arm. Earlier entries
        # remain: they represent enclosing branches or preceding guards whose
        # continuing arm is genuinely required to reach this route.
        terminal_orders = [
            group_order[requirement.groupId]
            for requirement in requirements
            if requirement.groupId in group_order
            and arm_exit_kinds(arms[(requirement.groupId, requirement.armLabel)])
            in ({"return"}, {"throw"})
        ]
        if terminal_orders:
            cutoff = min(terminal_orders)
            requirements = [
                requirement for requirement in requirements
                if group_order.get(requirement.groupId, cutoff) <= cutoff
            ]
        edges.append(dataclasses.replace(edge, branchRequirements=requirements))
    return dataclasses.replace(method, sequenceEdges=edges)


def materialize_method_empty_arm_routes(method: MethodDefinition) -> MethodDefinition:
    """Create missing local skip edges for zero-call normal branch arms."""
    nodes = {method.entry.id: method.entry, **{node.id: node for node in method.nodes}}
    members = _members_by_arm(method)
    edges = list(method.sequenceEdges)
    group_order = {group.id: index for index, group in enumerate(method.branchGroups)}
    arms_by_key = {
        (group.id, arm.label): arm
        for group in method.branchGroups
        for arm in group.arms
    }
    existing = {
        (
            edge.source,
            edge.target,
            tuple((item.groupId, item.armLabel) for item in edge.branchRequirements),
        )
        for edge in edges
        if edge.type == "sequence"
    }
    rebuilt_groups: list[BranchGroup] = []

    def route_can_select(
        edge: Edge,
        group: BranchGroup,
        arm: BranchArm,
    ) -> bool:
        current_order = group_order[group.id]
        for requirement in edge.branchRequirements:
            if requirement.groupId == group.id:
                if requirement.armLabel != arm.label:
                    return False
                continue
            required_arm = arms_by_key.get(
                (requirement.groupId, requirement.armLabel)
            )
            required_order = group_order.get(requirement.groupId)
            if (
                required_arm is not None
                and required_order is not None
                and required_order < current_order
                and arm_exit_kinds(required_arm) in ({"return"}, {"throw"})
            ):
                return False
        return True

    def target_can_select(
        source_ids: list[str],
        target: str,
        group: BranchGroup,
        arm: BranchArm,
    ) -> bool:
        for source in source_ids:
            physical_edges = [
                edge for edge in edges
                if edge.type == "sequence"
                and edge.source == source
                and edge.target == target
            ]
            if not physical_edges or any(
                route_can_select(edge, group, arm) for edge in physical_edges
            ):
                return True
        return False

    for group in method.branchGroups:
        group_members = set().union(*(
            members.get((group.id, arm.label), set()) for arm in group.arms
        )) if group.arms else set()
        boundary_targets = list(dict.fromkeys(
            edge.target
            for edge in edges
            if edge.type == "sequence"
            and edge.source in group_members
            and edge.target not in group_members
            and nodes.get(edge.target) is not None
            and nodes[edge.target].exitKind != "throw"
        ))
        direct_outside_targets = list(dict.fromkeys(
            edge.target
            for edge in edges
            if edge.type == "sequence"
            and edge.source in group.branchPointIds
            and edge.target not in group_members
            and nodes.get(edge.target) is not None
            and nodes[edge.target].exitKind != "throw"
        ))
        fallthrough_targets = [
            node.id for node in method.nodes if node.exitKind == "fallthrough"
        ]

        arms: list[BranchArm] = []
        for arm in group.arms:
            if not arm.empty or arm_exit_kinds(arm) != {"continues"}:
                arms.append(arm)
                continue
            # Prefer the closest evidence. Direct successors are already the
            # executable skip route; boundary targets are inferred from a
            # concrete continuing arm; fallthrough is only the final fallback.
            target_candidates = (
                list(arm.targetIds or ())
                or direct_outside_targets
                or boundary_targets
                or fallthrough_targets
            )
            targets = list(dict.fromkeys(target_candidates))
            # A shared surviving predecessor can anchor consecutive guards.
            # Do not reinterpret an earlier terminal edge, or the opposite
            # arm of this group, as this empty arm's continuation.
            targets = [
                target for target in targets
                if target_can_select(group.branchPointIds, target, group, arm)
            ]
            rebuilt_arm = dataclasses.replace(arm, targetIds=targets)
            arms.append(rebuilt_arm)
            own_requirement = BranchRequirement(group.id, arm.label)
            for source in group.branchPointIds:
                source_requirements = [
                    requirement
                    for requirement in _requirements_for_node(nodes.get(source))
                    if requirement.groupId != group.id
                ]
                requirements = _merge_requirements(source_requirements, [own_requirement])
                for target in targets:
                    route_requirements = _merge_requirements(
                        requirements,
                        _requirements_for_node(nodes.get(target)),
                    )
                    physical_edge_index = next(
                        (
                            index for index, edge in enumerate(edges)
                            if edge.type == "sequence"
                            and edge.source == source
                            and edge.target == target
                            and route_can_select(edge, group, arm)
                        ),
                        None,
                    )
                    if physical_edge_index is not None:
                        physical_edge = edges[physical_edge_index]
                        edges[physical_edge_index] = dataclasses.replace(
                            physical_edge,
                            branchRequirements=_merge_requirements(
                                list(physical_edge.branchRequirements),
                                route_requirements,
                            ),
                        )
                        continue
                    key = (
                        source,
                        target,
                        tuple((item.groupId, item.armLabel) for item in route_requirements),
                    )
                    if source == target or key in existing:
                        continue
                    edges.append(Edge(
                        source=source,
                        target=target,
                        type="sequence",
                        branchRequirements=route_requirements,
                    ))
                    existing.add(key)
        rebuilt_groups.append(dataclasses.replace(group, arms=arms))

    return dataclasses.replace(
        method,
        sequenceEdges=edges,
        branchGroups=rebuilt_groups,
    )


def prepare_method_branch_routes(method: MethodDefinition) -> MethodDefinition:
    """Build the complete method-local branch execution contract."""
    method = recompute_method_branch_geometry(method)
    method = annotate_method_branch_requirements(method)
    return materialize_method_empty_arm_routes(method)


def prepare_all_method_branch_routes(
    methods: dict[str, MethodDefinition],
) -> dict[str, MethodDefinition]:
    return {
        entry_id: prepare_method_branch_routes(method)
        for entry_id, method in methods.items()
    }
