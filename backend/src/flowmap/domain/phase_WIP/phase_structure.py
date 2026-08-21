from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field

from model import Graph, NodeSemanticFeatures, OperationRole


@dataclass(frozen=True, slots=True)
class StraightLineScope:
    id: str
    methodEntryId: str
    method: str | None
    nodeIds: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class BranchRegionScope:
    """One BranchGroup viewed as a structural phase candidate.

    ``groupId`` is also the virtual connectivity anchor. It connects the
    alternative ``armNodeIds`` without requiring a branch-point operation to
    become a phase member.
    """

    id: str
    groupId: str
    method: str | None
    methodEntryId: str | None
    nodeIds: tuple[str, ...]
    armNodeIds: dict[str, tuple[str, ...]]
    branchPointIds: tuple[str, ...]
    convergesAt: str | None
    parentGroupId: str | None = None
    childGroupIds: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class StructuralScopes:
    straightLines: tuple[StraightLineScope, ...] = ()
    branchRegions: tuple[BranchRegionScope, ...] = ()

    def branch(self, group_id: str) -> BranchRegionScope:
        return next(scope for scope in self.branchRegions if scope.groupId == group_id)


@dataclass(frozen=True, slots=True)
class OperationClassification:
    nodeId: str
    role: OperationRole
    evidence: tuple[str, ...] = ()
    confidence: float = 1.0
    ambiguous: bool = False


@dataclass(frozen=True, slots=True)
class BranchPurpose:
    groupId: str
    purposefulArmLabels: tuple[str, ...] = ()
    exceptionOnlyArmLabels: tuple[str, ...] = ()
    isGuard: bool = False


@dataclass(frozen=True, slots=True)
class OperationClassificationResult:
    graph: Graph
    operations: dict[str, OperationClassification] = field(default_factory=dict)
    branches: dict[str, BranchPurpose] = field(default_factory=dict)
    ignoredFallbackEdges: frozenset[tuple[str, str]] = frozenset()


def _plain_sequence_out(graph: Graph) -> dict[str, list[str]]:
    outgoing: dict[str, list[str]] = {}
    for edge in graph.edges:
        if edge.type == "sequence" and edge.returnFrom is None:
            outgoing.setdefault(edge.source, []).append(edge.target)
    return outgoing


def _method_instances(graph: Graph) -> dict[str, set[str]]:
    """Nodes belonging to each entry's own method instance.

    Invoke edges and returnFrom edges are deliberately excluded: local phase
    comparisons remain between siblings in one method instance even though
    final membership is later spliced into the flattened visual graph.
    """
    outgoing = _plain_sequence_out(graph)
    instances: dict[str, set[str]] = {}
    for entry in (node for node in graph.nodes if node.type == "entry"):
        reached: set[str] = set()
        stack = [entry.id]
        while stack:
            node_id = stack.pop()
            if node_id in reached:
                continue
            reached.add(node_id)
            stack.extend(outgoing.get(node_id, ()))
        instances[entry.id] = reached
    return instances


def build_structural_scopes(graph: Graph) -> StructuralScopes:
    """Build method-local linear scopes and nested BranchGroup regions."""
    nodes_by_id = {node.id: node for node in graph.nodes}
    node_rank = {node.id: index for index, node in enumerate(graph.nodes)}
    instances = _method_instances(graph)

    members_by_arm: dict[tuple[str, str], set[str]] = {}
    members_by_group: dict[str, set[str]] = {}
    for node in graph.nodes:
        if node.type != "call":
            continue
        for tag in node.branchArms:
            members_by_arm.setdefault((tag.groupId, tag.armLabel), set()).add(node.id)
            members_by_group.setdefault(tag.groupId, set()).add(node.id)

    def instance_for(group_id: str, points: tuple[str, ...]) -> str | None:
        anchors = set(points) | members_by_group.get(group_id, set())
        candidates = [
            entry_id for entry_id, member_ids in instances.items()
            if anchors & member_ids
        ]
        return min(candidates, key=lambda node_id: node_rank.get(node_id, 10**9)) \
            if candidates else None

    parent_by_group: dict[str, str | None] = {}
    groups_by_id = {group.id: group for group in graph.branchGroups}
    for child in graph.branchGroups:
        child_members = members_by_group.get(child.id, set())
        child_anchors = child_members | set(child.branchPointIds)
        candidates = []
        for parent in graph.branchGroups:
            if parent.id == child.id:
                continue
            parent_members = members_by_group.get(parent.id, set())
            if not child_anchors or not child_anchors <= parent_members:
                continue
            # Nested source groups normally appear later than their parent.
            if (
                parent.line is not None and child.line is not None
                and parent.line > child.line
            ):
                continue
            candidates.append(parent)
        parent_by_group[child.id] = min(
            candidates,
            key=lambda group: (
                len(members_by_group.get(group.id, set())),
                -(group.line if group.line is not None else -1),
            ),
            default=None,
        ).id if candidates else None

    children_by_group: dict[str, list[str]] = {}
    for child_id, parent_id in parent_by_group.items():
        if parent_id is not None:
            children_by_group.setdefault(parent_id, []).append(child_id)

    branch_regions: list[BranchRegionScope] = []
    for group in graph.branchGroups:
        arm_members = {
            arm.label: tuple(sorted(
                members_by_arm.get((group.id, arm.label), ()),
                key=lambda node_id: node_rank.get(node_id, 10**9),
            ))
            for arm in group.arms
        }
        node_ids = tuple(sorted(
            members_by_group.get(group.id, ()),
            key=lambda node_id: node_rank.get(node_id, 10**9),
        ))
        method_entry_id = instance_for(group.id, tuple(group.branchPointIds))
        branch_regions.append(BranchRegionScope(
            id=f"branch:{group.id}",
            groupId=group.id,
            method=group.method,
            methodEntryId=method_entry_id,
            nodeIds=node_ids,
            armNodeIds=arm_members,
            branchPointIds=tuple(group.branchPointIds),
            convergesAt=group.convergesAt,
            parentGroupId=parent_by_group[group.id],
            childGroupIds=tuple(sorted(
                children_by_group.get(group.id, ()),
                key=lambda group_id: (
                    groups_by_id[group_id].line
                    if groups_by_id[group_id].line is not None else -1
                ),
            )),
        ))

    all_branch_members = set().union(*members_by_group.values()) \
        if members_by_group else set()
    sequence_out = _plain_sequence_out(graph)
    straight_lines: list[StraightLineScope] = []
    for entry_id, instance_ids in instances.items():
        method = nodes_by_id[entry_id].calleeFullName
        eligible = {
            node_id for node_id in instance_ids
            if node_id in nodes_by_id
            and nodes_by_id[node_id].type == "call"
            and node_id not in all_branch_members
        }
        outgoing = {
            node_id: [target for target in sequence_out.get(node_id, ()) if target in eligible]
            for node_id in eligible
        }
        incoming: dict[str, list[str]] = {node_id: [] for node_id in eligible}
        for source, targets in outgoing.items():
            for target in targets:
                incoming[target].append(source)

        starts = sorted(
            (
                node_id for node_id in eligible
                if len(incoming[node_id]) != 1
                or len(outgoing[incoming[node_id][0]]) != 1
            ),
            key=lambda node_id: node_rank.get(node_id, 10**9),
        )
        visited: set[str] = set()

        def consume(start: str) -> tuple[str, ...]:
            path: list[str] = []
            current = start
            while current not in visited:
                visited.add(current)
                path.append(current)
                successors = outgoing[current]
                if len(successors) != 1 or len(incoming[successors[0]]) != 1:
                    break
                current = successors[0]
            return tuple(path)

        paths = [consume(start) for start in starts if start not in visited]
        # Covers a method-local cycle with no degree-defined start.
        for node_id in sorted(eligible - visited, key=lambda n: node_rank.get(n, 10**9)):
            if node_id not in visited:
                paths.append(consume(node_id))

        for index, node_ids in enumerate(paths):
            straight_lines.append(StraightLineScope(
                id=f"straight:{entry_id}:{index}",
                methodEntryId=entry_id,
                method=method,
                nodeIds=node_ids,
            ))

    return StructuralScopes(tuple(straight_lines), tuple(branch_regions))


def _exception_constructor(callee: str | None, receiver_type: str | None) -> bool:
    if callee and ".<init>" not in callee:
        return False
    candidates = []
    if callee:
        candidates.append(callee.split(":", 1)[0].split(".<init>", 1)[0])
    if receiver_type:
        candidates.append(receiver_type)
    return any(
        value.rsplit(".", 1)[-1].endswith(("Exception", "Error", "Throwable"))
        for value in candidates
    )


def _message_or_allocation_helper(callee: str | None) -> bool:
    if not callee:
        return False
    signature_free = callee.split(":", 1)[0]
    method = signature_free.rsplit(".", 1)[-1]
    receiver = signature_free.rsplit(".", 1)[0]
    return (
        callee in {"<operator>.alloc", "<operator>.assignment"}
        or method in {"format", "concat", "append", "toString", "valueOf"}
        or receiver.endswith(("String", "StringBuilder", "StringBuffer", "Formatter"))
    )


def classify_operation_roles(graph: Graph) -> OperationClassificationResult:
    """Systematically classify structural, atomic, and exception operations."""
    nodes_by_id = {node.id: node for node in graph.nodes}
    invoke_out: dict[str, list[str]] = {}
    sequence_out = _plain_sequence_out(graph)
    data_out: dict[str, list[str]] = {}
    for edge in graph.edges:
        if edge.type == "invoke":
            invoke_out.setdefault(edge.source, []).append(edge.target)
        elif edge.type == "data":
            data_out.setdefault(edge.source, []).append(edge.target)

    throwing_arms = {
        (group.id, arm.label)
        for group in graph.branchGroups
        for arm in group.arms
        if arm.terminus == "throw"
    }
    throwing_members: dict[tuple[str, str], set[str]] = {
        key: set() for key in throwing_arms
    }
    for node in graph.nodes:
        for tag in node.branchArms:
            key = (tag.groupId, tag.armLabel)
            if key in throwing_members:
                throwing_members[key].add(node.id)

    throwing_node_ids = {
        node_id for members in throwing_members.values() for node_id in members
    }

    exception_constructor_ids = {
        node.id for node in graph.nodes
        if node.type == "call"
        and (node.deadEnd or node.id in throwing_node_ids)
        and _exception_constructor(
            node.calleeFullName,
            graph.semanticFeatures.get(node.id, NodeSemanticFeatures()).receiverType,
        )
    }

    def reaches_exception_constructor(source: str, allowed: set[str]) -> bool:
        seen: set[str] = set()
        stack = [source]
        while stack:
            node_id = stack.pop()
            if node_id in seen:
                continue
            seen.add(node_id)
            if node_id != source and node_id in exception_constructor_ids:
                return True
            stack.extend(
                target for target in data_out.get(node_id, ())
                if target in allowed
            )
        return False

    operations: dict[str, OperationClassification] = {}
    for node in graph.nodes:
        if node.type in ("entry", "leaf"):
            operations[node.id] = OperationClassification(
                node.id, "structural", (f"node-type:{node.type}",)
            )
            continue

        evidence: list[str] = []
        role: OperationRole
        ambiguous = False
        containing_throw_arms = [
            key for key, members in throwing_members.items() if node.id in members
        ]
        if node.calleeFullName == "<operator>.throw":
            role = "exception-mechanic"
            evidence.append("explicit-throw-operator")
        elif node.id in exception_constructor_ids:
            role = "exception-mechanic"
            evidence.append("exception-constructor")
        else:
            is_mechanic_helper = any(
                _message_or_allocation_helper(node.calleeFullName)
                and reaches_exception_constructor(node.id, throwing_members[key])
                for key in containing_throw_arms
            )
            if is_mechanic_helper:
                role = "exception-mechanic"
                evidence.append("feeds-exception-constructor")
            else:
                internal_entries = [
                    target for target in invoke_out.get(node.id, ())
                    if nodes_by_id[target].type == "entry"
                ]
                has_visible_internal_work = any(
                    any(
                        nodes_by_id.get(reached) is not None
                        and nodes_by_id[reached].type == "call"
                        for reached in _reachable(entry, sequence_out)
                    )
                    for entry in internal_entries
                )
                if has_visible_internal_work:
                    role = "expanded-container"
                    evidence.append("invokes-visible-internal-work")
                elif internal_entries:
                    role = "atomic"
                    evidence.append("internal-callee-has-no-visible-work")
                else:
                    role = "atomic"
                    evidence.append("no-internal-callee")
                if containing_throw_arms and node.deadEnd:
                    ambiguous = True
                    evidence.append("purpose-before-throw-needs-semantic-review")

        operations[node.id] = OperationClassification(
            node.id,
            role,
            tuple(evidence),
            0.6 if ambiguous else 1.0,
            ambiguous,
        )

    branches: dict[str, BranchPurpose] = {}
    for group in graph.branchGroups:
        purposeful: list[str] = []
        exception_only: list[str] = []
        for arm in group.arms:
            members = [
                node_id for node_id in throwing_members.get((group.id, arm.label), ())
                if node_id in operations
            ] if arm.terminus == "throw" else []
            if arm.terminus == "throw" and all(
                operations[node_id].role == "exception-mechanic" for node_id in members
            ):
                exception_only.append(arm.label)
            else:
                purposeful.append(arm.label)
        branches[group.id] = BranchPurpose(
            groupId=group.id,
            purposefulArmLabels=tuple(purposeful),
            exceptionOnlyArmLabels=tuple(exception_only),
            isGuard=bool(exception_only) and len(purposeful) == 1,
        )

    updated_features = dict(graph.semanticFeatures)
    for node_id, classification in operations.items():
        if nodes_by_id[node_id].type != "call":
            continue
        current = updated_features.get(node_id, NodeSemanticFeatures())
        updated_features[node_id] = dataclasses.replace(
            current, role=classification.role
        )

    ignored_fallbacks: set[tuple[str, str]] = set()
    for edge in graph.edges:
        if not edge.fallback or edge.returnFrom is None:
            continue
        internal_entries = [
            target for target in invoke_out.get(edge.returnFrom, ())
            if nodes_by_id[target].type == "entry"
        ]
        internal_calls = {
            node_id for entry in internal_entries
            for node_id in _reachable(entry, sequence_out)
            if nodes_by_id.get(node_id) is not None
            and nodes_by_id[node_id].type == "call"
        }
        if internal_calls and all(
            operations[node_id].role == "exception-mechanic"
            for node_id in internal_calls
        ):
            ignored_fallbacks.add((edge.source, edge.target))

    return OperationClassificationResult(
        graph=dataclasses.replace(graph, semanticFeatures=updated_features),
        operations=operations,
        branches=branches,
        ignoredFallbackEdges=frozenset(ignored_fallbacks),
    )


def _reachable(start: str, outgoing: dict[str, list[str]]) -> set[str]:
    reached: set[str] = set()
    stack = [start]
    while stack:
        node_id = stack.pop()
        if node_id in reached:
            continue
        reached.add(node_id)
        stack.extend(outgoing.get(node_id, ()))
    return reached
