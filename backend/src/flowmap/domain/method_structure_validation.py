from __future__ import annotations

from model import BranchRequirement, MethodDefinition, Node


def _requirements_for_node(node: Node) -> list[BranchRequirement]:
    return [
        BranchRequirement(ref.groupId, ref.armLabel)
        for ref in node.branchArms
    ]


def validate_method_structures(method: MethodDefinition) -> MethodDefinition:
    """Validate extraction-owned structure topology without rewriting it."""
    nodes = {method.entry.id: method.entry}
    for node in method.nodes:
        if node.id in nodes:
            raise ValueError(
                f"Method {method.methodFullName!r} has duplicate node {node.id!r}"
            )
        nodes[node.id] = node

    branch_groups = {group.id: group for group in method.branchGroups}
    loop_groups = {group.id: group for group in method.loopGroups}
    if len(branch_groups) != len(method.branchGroups):
        raise ValueError(
            f"Method {method.methodFullName!r} has duplicate branch group IDs"
        )
    if len(loop_groups) != len(method.loopGroups):
        raise ValueError(
            f"Method {method.methodFullName!r} has duplicate loop group IDs"
        )
    overlap = set(branch_groups) & set(loop_groups)
    if overlap:
        raise ValueError(
            f"Method {method.methodFullName!r} reuses structure IDs "
            f"{sorted(overlap)!r}"
        )

    valid_arm_labels = {
        group.id: {arm.label for arm in group.arms}
        for group in method.branchGroups
    }
    for group in method.branchGroups:
        if len(valid_arm_labels[group.id]) != len(group.arms):
            raise ValueError(f"Branch {group.id!r} has duplicate arm labels")
        if group.entryNodeId is None:
            raise ValueError(f"Branch {group.id!r} has no entry anchor")
        entry = nodes.get(group.entryNodeId)
        if (
            entry is None
            or entry.type != "structure"
            or entry.structureGroupId != group.id
            or entry.structureRole != "entry"
        ):
            raise ValueError(f"Branch {group.id!r} has an invalid entry anchor")
        if group.exitNodeId is not None:
            exit_node = nodes.get(group.exitNodeId)
            if (
                exit_node is None
                or exit_node.type != "structure"
                or exit_node.structureGroupId != group.id
                or exit_node.structureRole != "exit"
            ):
                raise ValueError(f"Branch {group.id!r} has an invalid exit anchor")
        if group.enclosingRequirements != [
            requirement
            for requirement in _requirements_for_node(entry)
            if requirement.groupId != group.id
        ]:
            raise ValueError(
                f"Branch {group.id!r} enclosure disagrees with its entry anchor"
            )
        decision_ids = {
            node.id for node in nodes.values()
            if node.type == "structure"
            and node.structureGroupId == group.id
            and node.structureRole == "decision"
        }
        if not decision_ids:
            raise ValueError(f"Branch {group.id!r} has no structural decision")
        for decision_id in {
            stage.decisionNodeId
            for stage in group.conditionStages
            if stage.decisionNodeId is not None
        }:
            decision = nodes.get(decision_id)
            if (
                decision is None
                or decision.type != "structure"
                or decision.structureGroupId != group.id
                or decision.structureRole != "decision"
            ):
                raise ValueError(
                    f"Branch {group.id!r} has invalid decision {decision_id!r}"
                )
        for arm in group.arms:
            if not arm.exits:
                raise ValueError(
                    f"Branch {group.id!r} arm {arm.label!r} has no ArmExit"
                )
            for exit_ in arm.exits:
                if (
                    exit_.destinationNodeId is not None
                    and exit_.destinationNodeId not in nodes
                ):
                    raise ValueError(
                        f"Branch {group.id!r} arm {arm.label!r} exits to "
                        f"unknown node {exit_.destinationNodeId!r}"
                    )
                if (
                    exit_.kind in {"continues", "break", "continue"}
                    and exit_.destinationNodeId is None
                ):
                    raise ValueError(
                        f"Branch {group.id!r} arm {arm.label!r} has a "
                        f"destinationless {exit_.kind} exit"
                    )

    for group in method.loopGroups:
        for anchor_id, role in (
            (group.entryNodeId, "entry"),
            (group.exitNodeId, "exit"),
        ):
            anchor = nodes.get(anchor_id) if anchor_id is not None else None
            if (
                anchor is None
                or anchor.type != "structure"
                or anchor.structureGroupId != group.id
                or anchor.structureRole != role
            ):
                raise ValueError(
                    f"Loop {group.id!r} has an invalid {role} anchor"
                )

    for node in nodes.values():
        for ref in node.branchArms:
            if ref.groupId not in branch_groups:
                raise ValueError(
                    f"Node {node.id!r} references unknown branch {ref.groupId!r}"
                )
            if ref.armLabel not in valid_arm_labels[ref.groupId]:
                raise ValueError(
                    f"Node {node.id!r} references unknown arm "
                    f"{ref.groupId}.{ref.armLabel}"
                )
        for loop_id in node.loopIds:
            if loop_id not in loop_groups:
                raise ValueError(
                    f"Node {node.id!r} references unknown loop {loop_id!r}"
                )
        if (
            node.type == "transfer"
            and node.targetStructureGroupId not in loop_groups
        ):
            raise ValueError(
                f"Transfer {node.id!r} has an unknown target structure"
            )

    for edge in method.sequenceEdges:
        if edge.source not in nodes or edge.target not in nodes:
            raise ValueError(
                f"Method {method.methodFullName!r} has non-local sequence edge "
                f"{edge.source!r} -> {edge.target!r}"
            )
        selected: dict[str, str] = {}
        for requirement in edge.branchRequirements:
            labels = valid_arm_labels.get(requirement.groupId)
            if labels is None or requirement.armLabel not in labels:
                raise ValueError(
                    f"Edge {edge.source!r} -> {edge.target!r} has invalid "
                    f"requirement {requirement.groupId}.{requirement.armLabel}"
                )
            previous = selected.setdefault(
                requirement.groupId, requirement.armLabel
            )
            if previous != requirement.armLabel:
                raise ValueError(
                    f"Edge {edge.source!r} -> {edge.target!r} requires "
                    f"multiple arms of branch {requirement.groupId!r}"
                )
        source = nodes[edge.source]
        if source.type == "structure" and source.structureRole == "exit":
            if any(
                requirement.groupId == source.structureGroupId
                for requirement in edge.branchRequirements
            ):
                raise ValueError(
                    f"Edge leaving exit {source.id!r} retains its own branch scope"
                )

    return method


def validate_all_method_structures(
    methods: dict[str, MethodDefinition],
) -> dict[str, MethodDefinition]:
    for method in methods.values():
        validate_method_structures(method)
    return methods
