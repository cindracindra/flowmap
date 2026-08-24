import dataclasses
from collections import Counter

from domain.cfg_branching import _BranchTopology
from domain.cfg_traversal import _adjacency_out, _reachable_non_members, _walk_order
from model import BranchArm, BranchGroup, BranchRequirement, Edge, Node


def analyze_branch_routes(
    nodes: list[Node],
    edges: list[Edge],
    groups: list[BranchGroup],
    root_id: str,
) -> tuple[list[BranchGroup], list[Edge]]:
    """
    Resolve every presentation and execution route fact in one analysis.

    For each instance-scoped group this computes its convergence, each arm's
    visible exit targets, and the requirements placed on route edges. These
    facts used to be produced by separate convergence and annotation passes,
    with the latter reverse-engineering routes from the former.

    Only answerable here, not at filter time: a branch that ends its
    method converges on the CALLER's next call, and nothing connects the
    two until flattening synthesizes the returnFrom edge. It's per
    instance for the same reason -- the same branch inlined at two call
    sites rejoins in two different places.

    "Every path", not "two or more". They coincide at two arms but not at
    three: for `if / else if / else` where two arms continue past the
    branch and the third returns, a two-or-more rule picks the statement
    after the branch, which the returning arm never reaches. The real
    meeting point is further on, where the method itself returns to.

    An arm that throws contributes no path at all -- nothing follows a
    throw. A returning arm DOES contribute one: post-flatten it continues
    into its caller, which is exactly what this stage can see and the
    filter stage cannot.
    """
    topology = _BranchTopology.build(nodes, groups)
    sequence_out = _adjacency_out([e for e in edges if e.type == "sequence"])
    entry_ids = {n.id for n in nodes if n.type == "entry"}
    flow_out = _adjacency_out([
        e for e in edges
        if e.type == "sequence" or (e.type == "invoke" and e.target in entry_ids)
    ])

    # Where each call site's own flow RESUMES. A call whose callee was
    # inlined owns no sequence edge -- flattening replaced it with the
    # callee's return edge, which is tagged returnFrom with that call site.
    # So a fork that is itself a call has NO sequence successors at all
    # (confirmed live: `to.getBalance()` has zero), and enumerating its
    # continuations from sequence_out alone silently misses every one of
    # them, which is exactly the implicit-else path a branch needs to
    # converge against.
    continuation_out: dict[str, list[str]] = {}
    for edge in edges:
        if edge.type == "sequence" and edge.returnFrom is not None:
            continuation_out.setdefault(edge.returnFrom, []).append(edge.target)
    order = _walk_order(flow_out, [root_id], nodes)
    rank = lambda node_id: order.get(node_id, len(order))  # noqa: E731
    nodes_by_id = {n.id: n for n in nodes}

    catch_head_ids = {
        arm.firstCallId
        for group in groups if group.kind == "TRY"
        for arm in group.arms
        if arm.label != "noCatch" and arm.firstCallId is not None
    }
    requirements: list[list[BranchRequirement]] = [[] for _ in edges]

    def with_arm_targets(
        group: BranchGroup,
        convergence: str | None,
        implicit_targets: list[str] | None = None,
    ) -> BranchGroup:
        arms: list[BranchArm] = []
        for arm in group.arms:
            if arm.terminus == "throw":
                target_ids: list[str] = []
            elif arm.terminus == "return":
                target_ids = list(group.returnsTo)
            elif group.kind != "TRY" and arm.empty and implicit_targets:
                # The empty arm is represented by these direct route edges.
                # They are more precise than the eventual convergence point,
                # which may already have been reached through another caller
                # path and therefore rank before this inlined branch.
                target_ids = list(dict.fromkeys(implicit_targets))
            else:
                target_ids = [convergence] if convergence is not None else []
            arms.append(dataclasses.replace(arm, targetIds=target_ids))
        return dataclasses.replace(group, arms=arms, convergesAt=convergence)

    def record_routes(group: BranchGroup) -> None:
        """Project one resolved group's mutually-exclusive routes onto edges."""
        route_type = "invoke" if group.kind == "DISPATCH" else "sequence"
        shared_later_heads = topology.continuation_heads_after(group)
        explicit_by_target = {
            arm.firstCallId: arm.label
            for arm in group.arms
            if arm.firstCallId is not None
        }
        empty_arms = [arm for arm in group.arms if arm.firstCallId is None]

        for index, edge in enumerate(edges):
            if edge.type != route_type:
                continue
            point = (
                edge.source
                if edge.source in group.branchPointIds
                else edge.returnFrom
                if edge.returnFrom in group.branchPointIds
                else None
            )
            if point is None:
                continue

            arm_label = explicit_by_target.get(edge.target)
            if arm_label is None:
                matching_members = [
                    arm for arm in group.arms
                    if edge.target in topology.arm_members(group.id, arm.label)
                ]
                if len(matching_members) == 1:
                    arm_label = matching_members[0].label
                else:
                    matching_empty = [
                        arm for arm in empty_arms
                        if edge.target in (arm.targetIds or [])
                    ]
                    if not matching_empty and edge.target in shared_later_heads:
                        matching_empty = [
                            arm for arm in empty_arms
                            if arm.terminus == "continues"
                        ]
                    if len(matching_empty) != 1:
                        continue
                    arm_label = matching_empty[0].label

            requirement = BranchRequirement(group.id, arm_label)
            if requirement not in requirements[index]:
                requirements[index].append(requirement)

    resolved: list[BranchGroup] = []
    for group in groups:
        if not group.branchPointIds:
            resolved.append(with_arm_targets(group, None))
            continue

        group_members = set(topology.group_members(group.id))
        paths = [
            _reachable_non_members(
                arm.firstCallId, set(topology.arm_members(group.id, arm.label)),
                group_members, flow_out,
            )
            for arm in group.arms
            if arm.firstCallId is not None and arm.terminus != "throw"
        ]
        # Whatever the fork reaches without entering an arm: the implicit
        # else, or the normal continuation past a try.
        #
        # `deadEnd` is excluded for exactly the reason `terminus != "throw"`
        # excludes an arm above -- nothing follows a throw, so it is not a
        # live path and must not be counted as one. Applying it to arms
        # alone is not enough: after noise filtering strips the conditions,
        # two SEQUENTIAL branches in one method both anchor on the method
        # entry, so this enumeration sees the sibling group's arm heads
        # too. Confirmed live on BankAccountService.transfer, where the
        # fee chain picked up the preceding guard's
        # `new IllegalArgumentException(...)`: one dead path is enough to
        # empty the every-path intersection, and a branch that plainly
        # converges reported None.
        implicit_continuations = 0
        implicit_targets: list[str] = []
        later_sibling_heads = topology.continuation_heads_after(group)
        earlier_sibling_members = topology.members_before(group)
        for branch_point in group.branchPointIds:
            successors = list(sequence_out.get(branch_point, ()))
            successors += continuation_out.get(branch_point, ())
            for successor in successors:
                if successor in later_sibling_heads:
                    # This is a real direct route for an earlier group's
                    # empty continuing arm even when the later arm throws.
                    # Keep it as a target; dead ends are still excluded from
                    # the live-path convergence calculation below.
                    implicit_targets.append(successor)
                if (
                    successor in group_members
                    or successor in earlier_sibling_members
                    or nodes_by_id[successor].deadEnd
                    # A callee inside TRY is flattened with both its normal
                    # continuation and catch heads. A nested empty arm owns
                    # only normal completion; the enclosing TRY owns catches.
                    or (successor in catch_head_ids and successor not in group_members)
                ):
                    continue
                if successor not in later_sibling_heads:
                    implicit_targets.append(successor)
                paths.append(
                    _reachable_non_members(successor, set(), group_members, flow_out)
                )
                implicit_continuations += 1

        # ...unless the branch is the LAST thing in its method, where no
        # edge represents the skip at all -- a zero-call arm produces no
        # node and no edge (DESIGN.md #4.1). What it really does is fall
        # out of the method, so the instance's own continuation IS that
        # path. Only for an empty arm that CONTINUES: for a throwing or
        # returning one it would double-count the surviving route and
        # invent a convergence that isn't there.
        #
        # "LAST thing in its method" is what `implicit_continuations == 0`
        # tests, and testing it is load-bearing rather than defensive: when
        # the fork DOES have an in-method continuation, the empty arm skips
        # to THAT, and adding the enclosing frame's continuation as a
        # further path drags the answer a frame too high. Confirmed on the
        # same fee chain -- without this it resolves to the caller's
        # `report.recordSuccess(...)` instead of `from.withdraw(amount + fee)`.
        if any(arm.empty and arm.terminus == "continues" for arm in group.arms):
            if group.returnsTo and implicit_continuations == 0:
                implicit_targets.extend(group.returnsTo)
                paths.append(set(group.returnsTo))

        # Evaluated for ONE surviving path too, not just two or more.
        #
        # This reverses an earlier decision recorded above ("fewer than two
        # paths means a continuation, not a merge"). The rule below is
        # unchanged -- `count == len(paths)` is already "reached by every
        # live path" and `min(..., key=rank)` is already "earliest". The old
        # guard did not implement a different rule, it declined to evaluate
        # this one whenever a branch had a single live path, which is the
        # commonest shape there is: a guard clause, where the arm throws and
        # only the implicit false path survives.
        #
        # `convergesAt` therefore now means "where this branch stops
        # mattering" rather than "where two or more paths merge". For a
        # guard that is the first node the surviving path reaches, which is
        # exactly the bound a branch panel needs. Measured on the sample
        # project: 5 of 16 groups resolved before, 13 after (with the
        # continuation_out fix above), and no previously-resolved group
        # changed its answer.
        converges_at = None
        if paths:
            reached_by = Counter(node_id for path in paths for node_id in path)
            # After every fork: a loop's back edge makes nodes BEFORE the
            # branch reachable from each arm, and those are the loop
            # header, not a convergence.
            last_fork = max(rank(b) for b in group.branchPointIds)
            shared = [
                node_id for node_id, count in reached_by.items()
                if count == len(paths) and rank(node_id) > last_fork
            ]
            converges_at = min(shared, key=rank) if shared else None
        resolved_group = with_arm_targets(group, converges_at, implicit_targets)
        resolved.append(resolved_group)
        record_routes(resolved_group)

    annotated_edges = [
        dataclasses.replace(edge, branchRequirements=edge_requirements)
        for edge, edge_requirements in zip(edges, requirements, strict=True)
    ]
    return resolved, annotated_edges


def materialize_empty_arm_routes(
    nodes: list[Node], edges: list[Edge], groups: list[BranchGroup]
) -> list[Edge]:
    """Add executable sequence edges for resolved zero-call normal arms.

    Flattening can only return from concrete nodes it walks.  An empty arm
    at the end of a method has no node to become a tail, so branch analysis
    can resolve its target without there being an edge that can reach it.
    Materialize that inferred route after analysis, when instance-scoped
    targets and the surrounding returnFrom convention are both known.

    A call used as the branch condition is evaluated before either arm.  If
    it was internally inlined, its return edges identify the real resume
    sites; otherwise the branch point itself is the resume site.  When the
    target lies in the caller, copy returnFrom from a concrete arm's exit to
    preserve the existing inter-method return convention.
    """
    nodes_by_id = {node.id: node for node in nodes}
    materialized = list(edges)

    def requirement_key(requirements: list[BranchRequirement]) -> tuple[tuple[str, str], ...]:
        return tuple((requirement.groupId, requirement.armLabel) for requirement in requirements)

    existing = {
        (
            edge.source, edge.target, edge.type, edge.returnFrom,
            requirement_key(edge.branchRequirements),
        )
        for edge in materialized
    }

    for group in groups:
        group_members = {
            node.id for node in nodes
            if any(ref.groupId == group.id for ref in node.branchArms)
        }
        for arm in group.arms:
            if not arm.empty or arm.terminus != "continues":
                continue

            arm_requirement = BranchRequirement(group.id, arm.label)
            for target in arm.targetIds or []:
                # A real direct route already emitted by flattening only
                # needs the annotation added by _analyze_branch_routes.
                if any(
                    edge.type == "sequence"
                    and edge.target == target
                    and arm_requirement in edge.branchRequirements
                    for edge in materialized
                ):
                    continue

                # An internally-inlined condition resumes from its callee
                # tails.  A direct/external condition resumes at the call
                # site itself.
                resume_sites: list[
                    tuple[str, str | None, tuple[BranchRequirement, ...]]
                ] = []
                for point in group.branchPointIds:
                    returned = [
                        (
                            edge.source,
                            point,
                            tuple(
                                requirement for requirement in edge.branchRequirements
                                if requirement.groupId != group.id
                            ),
                        )
                        for edge in materialized
                        if edge.type == "sequence"
                        and edge.returnFrom == point
                        and any(
                            requirement.groupId == group.id
                            for requirement in edge.branchRequirements
                        )
                    ]
                    direct_templates = [
                        tuple(
                            requirement for requirement in edge.branchRequirements
                            if requirement.groupId != group.id
                        )
                        for edge in materialized
                        if edge.type == "sequence"
                        and edge.source == point
                        and any(
                            requirement.groupId == group.id
                            for requirement in edge.branchRequirements
                        )
                    ]
                    resume_sites.extend(
                        returned
                        or [(point, None, requirements) for requirements in direct_templates]
                        or [(point, None, ())]
                    )

                # If the empty arm falls out of its method, concrete arm
                # exits to the same target reveal the enclosing call site
                # that returnFrom must name.
                target_return_from = next(
                    (
                        edge.returnFrom
                        for edge in materialized
                        if edge.type == "sequence"
                        and edge.target == target
                        and edge.source in group_members
                        and edge.returnFrom is not None
                    ),
                    None,
                )

                for source, condition_return_from, outer_requirements in dict.fromkeys(resume_sites):
                    if source not in nodes_by_id or target not in nodes_by_id:
                        continue
                    return_from = condition_return_from or target_return_from
                    requirements = [*outer_requirements, arm_requirement]
                    key = (
                        source, target, "sequence", return_from,
                        requirement_key(requirements),
                    )
                    if source == target or key in existing:
                        continue
                    materialized.append(Edge(
                        source=source,
                        target=target,
                        type="sequence",
                        returnFrom=return_from,
                        branchRequirements=requirements,
                    ))
                    existing.add(key)

    return materialized


