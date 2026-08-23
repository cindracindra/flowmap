import dataclasses

from domain.cfg_traversal import _adjacency_out, _walk_order
from model import BranchGroup, BranchArm, Edge, Node


@dataclasses.dataclass(frozen=True, slots=True)
class _BranchTopology:
    """Branch relations retained by the call-only CFG projection.

    Removing condition nodes can co-locate nested or sequential source
    branches on one visible branch point. Group identity still comes from
    Joern's control structures; source order determines which co-located
    groups can be reached after which others. Convergence and route
    annotation both consume this one representation.
    """

    groups: tuple[BranchGroup, ...]
    members_by_arm: dict[tuple[str, str], frozenset[str]]
    members_by_group: dict[str, frozenset[str]]
    source_rank: dict[str, tuple[int, int]]

    @classmethod
    def build(cls, nodes: list[Node], groups: list[BranchGroup]) -> "_BranchTopology":
        mutable_by_arm: dict[tuple[str, str], set[str]] = {}
        for node in nodes:
            for tag in node.branchArms:
                mutable_by_arm.setdefault((tag.groupId, tag.armLabel), set()).add(
                    node.id
                )

        members_by_arm = {
            key: frozenset(node_ids) for key, node_ids in mutable_by_arm.items()
        }
        mutable_by_group: dict[str, set[str]] = {}
        for (group_id, _), node_ids in members_by_arm.items():
            mutable_by_group.setdefault(group_id, set()).update(node_ids)

        return cls(
            groups=tuple(groups),
            members_by_arm=members_by_arm,
            members_by_group={
                group_id: frozenset(node_ids)
                for group_id, node_ids in mutable_by_group.items()
            },
            source_rank={
                group.id: (group.line if group.line is not None else -1, index)
                for index, group in enumerate(groups)
            },
        )

    def arm_members(self, group_id: str, arm_label: str) -> frozenset[str]:
        return self.members_by_arm.get((group_id, arm_label), frozenset())

    def group_members(self, group_id: str) -> frozenset[str]:
        return self.members_by_group.get(group_id, frozenset())

    def co_located(self, group: BranchGroup) -> tuple[BranchGroup, ...]:
        points = set(group.branchPointIds)
        return tuple(
            candidate
            for candidate in self.groups
            if candidate.id != group.id
            and candidate.method == group.method
            and bool(points & set(candidate.branchPointIds))
        )

    def before(self, group: BranchGroup) -> tuple[BranchGroup, ...]:
        rank = self.source_rank[group.id]
        return tuple(
            candidate
            for candidate in self.co_located(group)
            if self.source_rank[candidate.id] < rank
        )

    def after(self, group: BranchGroup) -> tuple[BranchGroup, ...]:
        rank = self.source_rank[group.id]
        return tuple(
            candidate
            for candidate in self.co_located(group)
            if self.source_rank[candidate.id] > rank
        )

    def members_before(self, group: BranchGroup) -> frozenset[str]:
        return frozenset(
            node_id
            for earlier in self.before(group)
            for node_id in self.group_members(earlier.id)
        )

    def continuation_heads_after(self, group: BranchGroup) -> frozenset[str]:
        """Heads of later co-located groups outside this group's arms.

        A later nested condition is also co-located after projection, but
        its heads remain members of the enclosing arm. Only a head outside
        the current group's member region is a sequential continuation that
        an empty arm can enter.
        """
        group_members = self.group_members(group.id)
        return frozenset(
            arm.firstCallId
            for later in self.after(group)
            for arm in later.arms
            if arm.firstCallId is not None and arm.firstCallId not in group_members
        )


def _recompute_branch_geometry(
    nodes: list[Node], edges: list[Edge], groups: list[BranchGroup]
) -> list[BranchGroup]:
    """
    Re-derives what a branch group's shape depends on which nodes actually
    survived: each arm's `empty`/`firstCallId`, and the group's
    `branchPointIds`.

    Arm membership needs no inference -- full_cfg.sc tags every call in an
    arm, so an arm's surviving content is just the nodes carrying its tag.
    Only the ORDER has to be recovered from the graph (see
    _walk_order).

    Runs even when nothing was filtered out: extraction's `firstCallId` is
    AST-first, which is the wrong node for a CFG view regardless of
    whether any noise was stripped.

    `convergesAt` is deliberately NOT computed here. Sequence edges don't
    cross methods at this stage, so a branch that is the last thing in its
    method -- a guard clause, an if/else ending a method -- converges on
    the CALLER's next call, which no edge reaches until flatten_cfg
    synthesizes the returnFrom edge. It is also per-call-site by nature: a
    method inlined twice converges in two different places, which one
    pre-clone value cannot express. It belongs to the flatten stage.
    """
    if not groups:
        return groups

    sequence_edges = [e for e in edges if e.type == "sequence"]
    order = _walk_order(
        _adjacency_out(sequence_edges),
        [n.id for n in nodes if n.type == "entry"],
        nodes,
    )
    sequence_out = _adjacency_out(sequence_edges)
    sequence_in: dict[str, list[str]] = {}
    for edge in sequence_edges:
        sequence_in.setdefault(edge.target, []).append(edge.source)

    topology = _BranchTopology.build(nodes, groups)

    rank = lambda node_id: order.get(node_id, len(order))  # noqa: E731

    rebuilt: list[BranchGroup] = []
    for group in groups:
        arms: list[BranchArm] = []
        for arm in group.arms:
            surviving = topology.arm_members(group.id, arm.label)
            head = min(surviving, key=rank) if surviving else None
            arms.append(
                dataclasses.replace(
                    arm,
                    empty=not surviving,
                    firstCallId=head,
                )
            )

        # The fork hangs off whatever leads INTO an arm without being part
        # of THAT arm. Excluding the arm's own members is what stops a loop
        # inside an arm from nominating its own tail (which points back at
        # its head) as the branch point. For TRY, the untagged try-tail is
        # the predecessor of each catch head and is therefore the desired
        # branch point.
        candidates: set[str] = set()
        for arm in arms:
            if arm.firstCallId is None:
                continue
            own_arm = topology.arm_members(group.id, arm.label)
            candidates |= {
                pred
                for pred in sequence_in.get(arm.firstCallId, ())
                if pred not in own_arm
            }

        # Of those, the ones that genuinely fork. An IF's condition has the
        # arms as its successors; a TRY's try-tail has the handler and the
        # normal continuation. What this rules out is the method entry,
        # which leads into the try body without being where anything splits.
        # When nothing forks there is no visible split -- the other path
        # has no surviving node (a guard at the end of a method, a try
        # whose method ends right after the catch). One anchor is enough
        # then, and it is the LATEST candidate: for a try that's the try
        # tail rather than the method entry, which is where the exception
        # would divert from.
        forking = {c for c in candidates if len(set(sequence_out.get(c, ()))) > 1}
        if forking:
            branch_points = sorted(forking, key=rank)
        else:
            branch_points = (
                [max(candidates, key=rank)]
                if candidates
                # Extraction supplies this fallback for a branch whose arms
                # contain no calls (notably `if (...) return;`).  There is no
                # tagged arm head from which this stage could rediscover it.
                else [point for point in group.branchPointIds if point in order]
            )

        # The try body and finally are ordinary flow outside this group.
        # With no surviving catch work, only the empty noCatch arm remains,
        # so TRY is transparent and should not leave behind a panel.
        if group.kind == "TRY" and not any(
            arm.label != "noCatch" and not arm.empty for arm in arms
        ):
            continue

        # Filtering can erase every operation in an IF while leaving the
        # extracted control-structure metadata behind.  When every arm then
        # continues normally, the branch is transparent in the projected
        # graph: retaining it would manufacture duplicate routes from the
        # preceding visible call in addition to that call's inlined return
        # route.  Do retain zero-call guards whose arms return or throw --
        # their different termini still encode observable control flow.
        if (
            group.kind == "IF"
            and arms
            and all(arm.empty and arm.terminus == "continues" for arm in arms)
        ):
            continue

        rebuilt.append(
            dataclasses.replace(
                group,
                arms=arms,
                branchPointIds=branch_points,
            )
        )
    return rebuilt
