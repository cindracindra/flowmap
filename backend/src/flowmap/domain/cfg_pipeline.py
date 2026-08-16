import dataclasses
import itertools
from collections import Counter, deque

from model import (
    BranchArm,
    BranchArmRef,
    BranchGroup,
    BranchRequirement,
    Edge,
    Graph,
    Node,
)
from domain.util import is_noise, is_jdk_call_site_strip


def classify_roots_and_orphans(graph: Graph) -> Graph:
    """Classify uncalled entries by their surviving executable flow.

    An entry with no incoming internal invocation is a root when its method
    owns at least one surviving sequence/invoke edge, otherwise an orphan.
    Data edges deliberately do not make a method operational.
    """

    nodes_by_id = {n.id: n for n in graph.nodes}
    entry_id_by_fullname = {
        n.calleeFullName: n.id for n in graph.nodes if n.type == "entry"
    }

    invoke_in: dict[str, int] = {}
    entries_with_executable_flow: set[str] = set()
    for e in graph.edges:
        if e.type not in ("sequence", "invoke"):
            continue
        source_node = nodes_by_id.get(e.source)
        if source_node is not None:
            if source_node.type == "entry":
                entries_with_executable_flow.add(source_node.id)
            elif source_node.callerMethod is not None:
                owner_id = entry_id_by_fullname.get(source_node.callerMethod)
                if owner_id is not None:
                    entries_with_executable_flow.add(owner_id)

        if e.type == "invoke" and nodes_by_id.get(e.target, None) is not None:
            if nodes_by_id[e.target].type == "entry":
                invoke_in[e.target] = invoke_in.get(e.target, 0) + 1

    entry_ids = [n.id for n in graph.nodes if n.type == "entry"]
    roots = sorted(
        id_
        for id_ in entry_ids
        if invoke_in.get(id_, 0) == 0 and id_ in entries_with_executable_flow
    )
    orphans = sorted(
        id_
        for id_ in entry_ids
        if invoke_in.get(id_, 0) == 0 and id_ not in entries_with_executable_flow
    )

    return dataclasses.replace(graph, roots=roots, orphans=orphans)


def find_roots_above(graph: Graph, anchor_id: str) -> list[str]:
    """Walks "invoke" edges backward from an "entry" node (anchor_id) 
    to find every true root -- an entry node with no caller -- that can
    reach it."""

    entry_ids = {n.id for n in graph.nodes if n.type == "entry"}
    if anchor_id not in entry_ids:
        raise ValueError(f"{anchor_id!r} is not an 'entry' node in this graph")

    nodes_by_id = {n.id: n for n in graph.nodes}
    entry_id_by_fullname = {n.calleeFullName: n.id for n in graph.nodes if n.type == "entry"}

    invoke_in: dict[str, list[str]] = {}
    for e in graph.edges:
        if e.type == "invoke":
            invoke_in.setdefault(e.target, []).append(e.source)

    roots: set[str] = set()
    visiting: set[str] = set()

    def walk_up(entry_id: str) -> None:
        if entry_id in visiting:
            return
        visiting.add(entry_id)

        caller_entry_ids: set[str] = set()
        for call_id in invoke_in.get(entry_id, []):
            call_node = nodes_by_id.get(call_id)
            if call_node is None or call_node.callerMethod is None:
                continue
            caller_entry_id = entry_id_by_fullname.get(call_node.callerMethod)
            if caller_entry_id is not None:
                caller_entry_ids.add(caller_entry_id)

        if not caller_entry_ids:
            roots.add(entry_id)
        else:
            for caller_entry_id in caller_entry_ids:
                walk_up(caller_entry_id)

    walk_up(anchor_id)
    return sorted(roots) if roots else [anchor_id]


def slice_from_root(graph: Graph, root_id: str) -> Graph:
    """Extracts the forward-reachable subgraph from root_id (an 
    "entry" node id) out of the full-codebase graph."""

    nodes_by_id = {n.id: n for n in graph.nodes}
    root = nodes_by_id[root_id]
    if root.type != "entry":
        raise ValueError(f"slice_from_root's root must be an 'entry' node, got {root.type!r}")

    forward: dict[str, list[str]] = {}
    for e in graph.edges:
        if e.type in ("sequence", "invoke"):
            forward.setdefault(e.source, []).append(e.target)

    reached: set[str] = set()
    stack = [root_id]
    while stack:
        node_id = stack.pop()
        if node_id in reached:
            continue
        reached.add(node_id)
        stack.extend(forward.get(node_id, []))

    sliced_nodes = [nodes_by_id[i] for i in reached]
    sliced_edges = [e for e in graph.edges if e.source in reached and e.target in reached]

    sliced_method_names = {n.calleeFullName for n in sliced_nodes if n.type == "entry"}
    tagged_group_ids = {t.groupId for n in sliced_nodes for t in n.branchArms}
    sliced_groups = [
        g for g in graph.branchGroups
        if (g.method in sliced_method_names if g.method is not None
            else g.id in tagged_group_ids)
    ]

    return Graph(
        entryPoint=root.calleeFullName,
        nodes=sliced_nodes,
        edges=sliced_edges,
        branchGroups=sliced_groups,
    )


def slice_anchored_cfg(graph: Graph, method_full_name: str) -> list[Graph]:
    """Extract sliced subgraph(s) given a method's full name."""

    entry_id_by_fullname = {n.calleeFullName: n.id for n in graph.nodes if n.type == "entry"}
    anchor_id = entry_id_by_fullname.get(method_full_name)
    if anchor_id is None:
        raise ValueError(f"{method_full_name!r} is not an 'entry' node in this graph")

    return [
        slice_from_root(graph, root_id)
        for root_id in find_roots_above(graph, anchor_id)
    ]


def _adjacency_out(edges: list[Edge]) -> dict[str, list[str]]:
    adjacency: dict[str, list[str]] = {}
    for edge in edges:
        adjacency.setdefault(edge.source, []).append(edge.target)
    return adjacency


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


def _reachable_non_members(
    start: str, own_arm: set[str], group_members: set[str], flow_out: dict[str, list[str]]
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
        _adjacency_out(sequence_edges), [n.id for n in nodes if n.type == "entry"], nodes
    )
    sequence_out = _adjacency_out(sequence_edges)
    sequence_in: dict[str, list[str]] = {}
    for edge in sequence_edges:
        sequence_in.setdefault(edge.target, []).append(edge.source)

    members: dict[tuple[str, str], set[str]] = {}
    for node in nodes:
        for tag in node.branchArms:
            members.setdefault((tag.groupId, tag.armLabel), set()).add(node.id)

    rank = lambda node_id: order.get(node_id, len(order))  # noqa: E731

    rebuilt: list[BranchGroup] = []
    for group in groups:
        arms: list[BranchArm] = []
        for arm in group.arms:
            surviving = members.get((group.id, arm.label), set())
            head = min(surviving, key=rank) if surviving else None
            arms.append(dataclasses.replace(
                arm, empty=not surviving, firstCallId=head,
            ))

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
            own_arm = members.get((group.id, arm.label), set())
            candidates |= {
                pred for pred in sequence_in.get(arm.firstCallId, ())
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
            branch_points = [max(candidates, key=rank)] if candidates else []

        # The try body and finally are ordinary flow outside this group.
        # With no surviving catch work, only the empty noCatch arm remains,
        # so TRY is transparent and should not leave behind a panel.
        if group.kind == "TRY" and not any(
            arm.label != "noCatch" and not arm.empty for arm in arms
        ):
            continue

        rebuilt.append(dataclasses.replace(
            group, arms=arms, branchPointIds=branch_points,
        ))
    return rebuilt


def _tag_dead_ends(nodes: list[Node]) -> list[Node]:
    """deadEnd=True iff this node's own `terminus` (set at extraction 
    time by full_cfg.sc's classifyTerminus) is "throw"."""

    return [
        dataclasses.replace(n, deadEnd=True) if n.terminus == "throw" else n
        for n in nodes
    ]


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
    excluded_ids = {
        n.id for n in cfg.nodes
        if n.type == "call"
        and n.calleeFullName is not None
        and (is_noise(n.calleeFullName) or is_jdk_call_site_strip(n.calleeFullName))
    }
    if not excluded_ids:
        return dataclasses.replace(
            cfg,
            nodes=_tag_dead_ends(cfg.nodes),
            branchGroups=_recompute_branch_geometry(cfg.nodes, cfg.edges, cfg.branchGroups),
        )

    throw_ids = {
        n.id for n in cfg.nodes
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
    root_ids = {n.id for n in kept_nodes if n.type == "entry" and n.calleeFullName == cfg.entryPoint}
    root_ids |= set(cfg.roots)
    if preserve_all_entries:
        root_ids |= {n.id for n in kept_nodes if n.type == "entry"}
    kept_nodes = [
        n for n in kept_nodes
        if n.id in connected_ids or n.id in root_ids
    ]

    return dataclasses.replace(
        cfg,
        nodes=kept_nodes,
        edges=kept_edges,
        branchGroups=_recompute_branch_geometry(kept_nodes, kept_edges, cfg.branchGroups),
    )


def filter_and_classify_roots_and_orphans(graph: Graph) -> Graph:
    """Two-pass whole-codebase operation analysis.

    The first pass records the raw structure. Filtering then keeps every
    method entry temporarily so entries exposed or emptied by filtering are
    available to the final classification. The returned graph is filtered
    and its roots/orphans describe that filtered graph.
    """
    initially_classified = classify_roots_and_orphans(graph)
    filtered = filter_noise_cfg(initially_classified, preserve_all_entries=True)
    return classify_roots_and_orphans(filtered)


def _scope_group_to_instance(
    group: BranchGroup, suffix: str, local_clone: dict[str, str],
    continuations: list[str],
) -> BranchGroup:
    """
    One inlined instance's copy of a branch group: `cs20` -> `cs20~7`,
    with every node id it holds re-pointed at that instance's clones.

    An arm whose head wasn't cloned here is empty FOR THIS INSTANCE --
    the trace simply doesn't contain it, whatever the pre-flatten graph
    said.
    """
    arms = [
        dataclasses.replace(
            arm,
            firstCallId=local_clone.get(arm.firstCallId) if arm.firstCallId else None,
            empty=local_clone.get(arm.firstCallId) is None if arm.firstCallId else True,
            targetIds=None,
        )
        for arm in group.arms
    ]
    return dataclasses.replace(
        group,
        id=f"{group.id}~{suffix}",
        arms=arms,
        branchPointIds=[
            local_clone[p] for p in group.branchPointIds if p in local_clone
        ],
        # Where this instance returns to, for a "return" arm's arrow and
        # for the zero-call-arm case in _resolve_convergence.
        returnsTo=list(continuations),
        convergesAt=None,  # needs the whole trace -- see _resolve_convergence
    )


def _resolve_convergence(
    nodes: list[Node], edges: list[Edge], groups: list[BranchGroup], root_id: str
) -> list[BranchGroup]:
    """
    Fills in each group's `convergesAt` -- the earliest node that EVERY
    live path out of the branch reaches.

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

    members: dict[tuple[str, str], set[str]] = {}
    for node in nodes:
        for tag in node.branchArms:
            members.setdefault((tag.groupId, tag.armLabel), set()).add(node.id)

    catch_head_ids = {
        arm.firstCallId
        for group in groups if group.kind == "TRY"
        for arm in group.arms
        if arm.label != "noCatch" and arm.firstCallId is not None
    }

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

    resolved: list[BranchGroup] = []
    for group in groups:
        if not group.branchPointIds:
            resolved.append(with_arm_targets(group, None))
            continue

        group_members = {
            node_id for arm in group.arms
            for node_id in members.get((group.id, arm.label), ())
        }
        paths = [
            _reachable_non_members(
                arm.firstCallId, members.get((group.id, arm.label), set()),
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
        later_sibling_heads = {
            arm.firstCallId
            for later in groups
            if later.id != group.id
            and later.method == group.method
            and set(later.branchPointIds) & set(group.branchPointIds)
            and (later.line or 0) > (group.line or 0)
            for arm in later.arms
            if arm.firstCallId is not None
        }
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
        resolved.append(with_arm_targets(group, converges_at, implicit_targets))
    return resolved


def _annotate_branch_requirements(
    edges: list[Edge], groups: list[BranchGroup]
) -> list[Edge]:
    """Stamp each fork/return edge with its backend-known arm selection.

    Node arm membership cannot describe an empty arm: after noise filtering
    there is no node to tag, and flattening represents normal completion by
    a fallback return edge.  This pass runs after instance scoping and
    convergence resolution, when both the real cloned branch points and all
    synthesized return edges exist.

    Explicit arm heads are matched by firstCallId. Empty/implicit routes are
    matched only against that arm's resolved targetIds. This distinction is
    essential for a call inside TRY: flattening gives its callee both the
    normal continuation and the catch head, but only the former belongs to
    the callee's empty normal arm; the latter belongs to the enclosing catch.
    """
    requirements: list[list[BranchRequirement]] = [
        list(edge.branchRequirements) for edge in edges
    ]

    for group in groups:
        route_type = "invoke" if group.kind == "DISPATCH" else "sequence"
        selectable = list(group.arms)
        shared_later_heads = {
            arm.firstCallId
            for later in groups
            if later.id != group.id
            and later.method == group.method
            and set(later.branchPointIds) & set(group.branchPointIds)
            and (later.line or 0) > (group.line or 0)
            for arm in later.arms
            if arm.firstCallId is not None
        }

        explicit_by_target = {
            arm.firstCallId: arm.label
            for arm in selectable
            if arm.firstCallId is not None
        }
        empty_arms = [arm for arm in selectable if arm.firstCallId is None]
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

            if edge.target in explicit_by_target:
                arm_label = explicit_by_target[edge.target]
            else:
                matching_empty = [
                    arm for arm in empty_arms
                    if edge.target in (arm.targetIds or [])
                ]
                if not matching_empty and edge.target in shared_later_heads:
                    # Stripped conditions can make sequential groups share
                    # one anchor. Reaching a later group's head means every
                    # earlier group took its empty continuing route.
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

    return [
        dataclasses.replace(edge, branchRequirements=edge_requirements)
        for edge, edge_requirements in zip(edges, requirements, strict=True)
    ]


def flatten_cfg(cfg: Graph) -> Graph:
    """
    Inlines every internally-traversed callee at its own call site into
    one continuous trace rooted at cfg.entryPoint. Ported unchanged in
    logic from processor.flatten_intermethod_cfg (raw-dict version,
    DESIGN.md #8 / PHASING_RULES.md), translated to Graph/Node/Edge --
    re-analysed for simplification first and left as-is: each mechanism
    below is load-bearing for a specific, previously-confirmed bug (see
    inline comments), and test_phaser.py has a dedicated fixture per
    mechanism.

    Key construction choices:
      - A call site's own "sequence" edge to whatever follows it is
        replaced by a synthesized "sequence" edge from the callee's own
        tail(s), tagged returnFrom with the original call site's id (so
        a phase-tree builder can evaluate the real call-site pair, not
        the tail).
      - A tail node tagged deadEnd (throw-truncated) gets no return edge
        at all -- it stays a genuine dead end.
      - Every method is cloned fresh per call site, never shared.
      - A tail call propagates its pending continuation through unchanged
        rather than minting a new one.
      - A method already being inlined higher up the same chain is cut
        off as a bare stub (recursion guard).
      - A callee whose whole inlined subtree never reaches the
        continuation it was given falls back to wiring the callee's own
        entry directly to it, tagged fallback=True.
      - Each clone's `depth` is the invoke-nesting level it is created
        at, stamped from `inline`'s own recursion -- see `clone`.
      - Branch groups are cloned per instance too, `cs20` -> `cs20~7`,
        with every id inside them re-pointed at this instance's clones --
        see `_scope_group_to_instance`. Without it, a method inlined at
        three call sites yields three copies of its groups all sharing
        one id, so "render group cs20" is ambiguous.
    """
    nodes_by_id = {n.id: n for n in cfg.nodes}

    root_original_id = next(
        n.id for n in cfg.nodes if n.type == "entry" and n.calleeFullName == cfg.entryPoint
    )
    sequence_out: dict[str, list[str]] = {}
    invoke_out: dict[str, list[str]] = {}
    data_out: dict[str, list[str]] = {}
    for e in cfg.edges:
        bucket = {"sequence": sequence_out, "invoke": invoke_out, "data": data_out}.get(e.type)
        if bucket is not None:
            bucket.setdefault(e.source, []).append(e.target)

    groups_by_method: dict[str, list[BranchGroup]] = {}
    for group in cfg.branchGroups:
        if group.method is not None:
            groups_by_method.setdefault(group.method, []).append(group)

    flat_nodes: dict[str, Node] = {}
    flat_edges: list[Edge] = []
    flat_groups: list[BranchGroup] = []
    seen_edges: set[tuple[str, str, str]] = set()
    id_counter = itertools.count()

    def emit_edge(
        from_id: str,
        to_id: str,
        edge_type: str,
        return_from: str | None = None,
        fallback: bool = False,
    ) -> None:
        key = (from_id, to_id, edge_type)
        if from_id == to_id or key in seen_edges:
            return
        seen_edges.add(key)
        flat_edges.append(
            Edge(
                source=from_id, target=to_id, type=edge_type,
                returnFrom=return_from, fallback=fallback,
            )
        )

    def clone(original_id: str, depth: int) -> str:
        """
        `depth` is the invoke-nesting level this particular clone is
        created at, passed down `inline`'s recursion rather than derived
        from any edge. It cannot be a property of the ORIGINAL node:
        a method invoked from two call sites at different nesting levels
        is cloned once per call site, and those clones belong in
        different columns. Anything keyed by original id (a shortest-path
        BFS over the pre-flatten graph, say) has one slot for N clones
        and necessarily collapses them onto the shallowest caller's
        level, rendering a call that doesn't advance a column at all.

        Not derivable from the FLATTENED graph's edges either, which is
        what makes the clone tree the only place this is unambiguous:
        a fallback/returnFrom-attributed "sequence" edge means "the
        containing method just returned", which no edge cost expresses
        (a tail-call chain three deep pops back to the ORIGINAL caller's
        level, not one per edge) -- DESIGN.md's "Sixth bug"/"Seventh bug"
        (§8) found that twice on the old Python visualizer.
        """
        new_id = f"{original_id}~{next(id_counter)}"
        flat_nodes[new_id] = dataclasses.replace(
            nodes_by_id[original_id], id=new_id, origId=original_id, depth=depth,
        )
        return new_id

    def inline(
        entry_original_id: str,
        continuations: list[str],
        return_from: str | None,
        visited_methods: frozenset[str],
        depth: int,
        inherited_tags: tuple[BranchArmRef, ...] = (),
    ) -> tuple[str, bool]:
        """
        Clones and wires ONE method's own reachable body (recursing into
        any internally-traversed invoke targets it encounters), returning
        (new id of its cloned entry node, whether `continuations` was ever
        actually reached from anywhere inside this method). `continuations`
        /`return_from` are what this method's own genuine tails (and any
        tail-call chain rooted here) should wire "return" edges to/be
        attributed to.

        The returned bool exists for the "fallback edge" rule (see the
        non-tail branch below): a caller that creates a NEW continuation
        for this call needs to know whether ANYTHING inside the whole
        recursively-inlined subtree actually used it, to decide whether a
        fallback is needed. True if either this method's own walk
        directly emitted a return edge using `continuations`, or a
        tail-call chain rooted here propagated through to something that
        did (recursively OR'd, never reset partway through a tail chain --
        propagating unchanged IS what makes a tail call a tail call).

        `depth` is this method's own invoke-nesting level (0 at the root).
        Every clone it makes is stamped with it -- see `clone`.
        """
        local_clone: dict[str, str] = {}

        def get_or_clone(original_id: str, at_depth: int = depth) -> str:
            # One method body is one column: "sequence" edges don't nest,
            # so the whole walk below shares this method's own `depth`.
            # Only crossing an "invoke" edge deepens, which is why the
            # leaf-target call site is the one place that overrides
            # `at_depth` (its internal-target counterpart deepens by
            # recursing into `inline` with depth + 1 instead).
            if original_id not in local_clone:
                local_clone[original_id] = clone(original_id, at_depth)
            return local_clone[original_id]

        entry_new_id = get_or_clone(entry_original_id)
        method_name = nodes_by_id[entry_original_id].calleeFullName

        # This instance's own group-id suffix, needed BEFORE the walk rather
        # than after it. A callee inlined from inside one of this method's
        # arms has to inherit that arm's INSTANCE-scoped id (`cs20~7`, not
        # `cs20`), and that recursion happens during the walk -- so the
        # rewrite can no longer wait until the end the way it used to.
        suffix = entry_new_id.rsplit("~", 1)[-1]
        scoped_ids = {g.id for g in groups_by_method.get(method_name, ())}

        def tags_for(original_id: str) -> list[BranchArmRef]:
            """
            Every arm this node belongs to: the ones inherited from the call
            site that inlined this whole method instance, plus the node's
            own, scoped to this instance.

            Inheritance is what makes arm membership mean "runs only when
            this arm is taken" rather than "is written inside this arm".
            Extraction tags calls LEXICALLY (`armRoot.ast.isCall`), so
            `if (x) foo();` tags one node while everything foo() does is
            untagged -- measured at 1 tagged against 9 untagged on the
            sample project's `reconcile(to)` arm.
            """
            seen: set[tuple[str, str]] = set()
            unique: list[BranchArmRef] = []
            own = (
                BranchArmRef(f"{t.groupId}~{suffix}", t.armLabel)
                if t.groupId in scoped_ids else t
                for t in nodes_by_id[original_id].branchArms
            )
            for tag in (*inherited_tags, *own):
                key = (tag.groupId, tag.armLabel)
                if key not in seen:
                    seen.add(key)
                    unique.append(tag)
            return unique

        if method_name in visited_methods:
            # Recursion cutoff: no body is inlined, but the stub represents
            # all deeper recursive execution and therefore consumes the
            # continuation propagated to it. Wiring that return here keeps
            # it on the cutoff entry; returning False would make the generic
            # caller fallback incorrectly originate at the first inlined
            # entry instead.
            if inherited_tags:
                flat_nodes[entry_new_id] = dataclasses.replace(
                    flat_nodes[entry_new_id], branchArms=tags_for(entry_original_id)
                )
            for continuation in continuations:
                emit_edge(
                    entry_new_id, continuation, "sequence",
                    return_from=return_from, fallback=True,
                )
            return entry_new_id, bool(continuations)

        # Leaves are keyed by CLONE id, not original: a leaf takes the arms
        # of the call site that invoked it, and two call sites in this same
        # method share one leaf clone (`local_clone` dedups by original id),
        # so their arms are unioned rather than one overwriting the other.
        leaf_tags: dict[str, list[BranchArmRef]] = {}

        continuation_consumed = False
        deeper_visited = visited_methods | {method_name}
        walked: set[str] = set()
        stack: list[str] = [entry_original_id]
        while stack:
            original_id = stack.pop()
            if original_id in walked:
                continue
            walked.add(original_id)
            this_new_id = get_or_clone(original_id)

            successors = sequence_out.get(original_id, [])
            invoke_targets = invoke_out.get(original_id, [])
            internal_targets = [
                t for t in invoke_targets if nodes_by_id[t].type == "entry"
            ]
            leaf_targets = [t for t in invoke_targets if nodes_by_id[t].type != "entry"]

            for leaf_target in leaf_targets:
                # An external callee has no body to inline, but the call
                # still nests -- so it deepens exactly like an internal
                # one, just without recursing. Safe to keep in
                # `local_clone` at a different depth than its neighbours:
                # a leaf is only ever an "invoke" target, never reached by
                # a "sequence" edge, so no id is requested at two depths.
                leaf_new_id = get_or_clone(leaf_target, depth + 1)
                emit_edge(this_new_id, leaf_new_id, "invoke")
                bucket = leaf_tags.setdefault(leaf_new_id, [])
                present = {(t.groupId, t.armLabel) for t in bucket}
                for tag in tags_for(original_id):
                    if (tag.groupId, tag.armLabel) not in present:
                        present.add((tag.groupId, tag.armLabel))
                        bucket.append(tag)

            if internal_targets:
                is_new_continuation = False
                if nodes_by_id[original_id].deadEnd:
                    # This call's OWN chain was throw-truncated -- e.g.
                    # `throw new InsufficientFundsException(...)`, where
                    # the exception class is project-owned, so this call
                    # site ALSO has an internal invoke target. That invoke
                    # still fires (the exception object really does get
                    # constructed, its own body is worth showing) but
                    # nothing it does may propagate any further
                    # continuation: throwing never returns normally,
                    # regardless of how many levels of construction/
                    # delegation happen first inside the thrown object's
                    # own constructor. Confirmed live: without this check,
                    # InsufficientFundsException's own `super(message)`
                    # tail was incorrectly wired to "return into" whatever
                    # happened to follow the throw SITE in the caller --
                    # since deadEnd-tagged nodes always have empty
                    # `successors` by construction, this branch takes
                    # priority over the successors check below, not just
                    # an additional case. No fallback here either -- a
                    # dead-end call site is a PROVEN terminus (a real
                    # throw, not an inferred one), unlike the "callee's
                    # WHOLE body dead-ends" case below.
                    callee_continuations: list[str] = []
                    callee_return_from = None
                elif successors:
                    # Non-tail: this call site's own next step(s) become
                    # the callee's continuation, and THIS call site (not
                    # whatever was passed into this `inline` call) is the
                    # new returnFrom. `is_new_continuation` marks this as
                    # a point where, if the callee's WHOLE inlined
                    # subtree never reaches this continuation, a fallback
                    # edge is warranted -- see below.
                    is_new_continuation = True
                    successor_clone_ids = [get_or_clone(s) for s in successors]
                    for s in successors:
                        if s not in walked:
                            stack.append(s)
                    callee_continuations = successor_clone_ids
                    callee_return_from = this_new_id
                else:
                    # Tail call: propagate this method's own pending
                    # continuation/returnFrom through unchanged.
                    callee_continuations = continuations
                    callee_return_from = return_from
                # Polymorphic dispatch: ONE call site, more than one real
                # implementation behind it. Nothing upstream models this as
                # a branch -- Joern's dynamic call linker resolves through
                # the type hierarchy and full_cfg.sc emits one invoke edge
                # per surviving implementation, and that edge multiplicity
                # is the only signal there is. It becomes a first-class
                # group here, so a consumer can treat "which implementation
                # runs" the same way it treats "which arm of the if runs",
                # instead of re-deriving it from edge counts.
                #
                # Recorded per CALL SITE CLONE, so the same interface call
                # reached twice in a trace yields two independent groups --
                # the same reason conditional groups are instance-scoped.
                dispatch_id = (
                    f"dispatch:{this_new_id}" if len(internal_targets) > 1 else None
                )
                dispatch_arms: list[BranchArm] = []

                for index, target in enumerate(internal_targets, start=1):
                    callee_tags = tags_for(original_id)
                    arm_label = f"impl{index}"
                    if dispatch_id is not None:
                        # The implementation's whole subtree is tagged as
                        # this arm's members, exactly like a conditional
                        # arm's, so arm membership stays one mechanism.
                        callee_tags = callee_tags + [
                            BranchArmRef(dispatch_id, arm_label)
                        ]
                    callee_entry_new, callee_consumed = inline(
                        target, callee_continuations, callee_return_from,
                        deeper_visited, depth + 1,
                        inherited_tags=tuple(callee_tags),
                    )
                    if dispatch_id is not None:
                        # No conditionCode: what selects this arm is the
                        # receiver's runtime type, not a source expression.
                        # `firstCallId` is the callee's own entry clone, so
                        # the implementation's identity is recoverable from
                        # that node's calleeFullName without duplicating it
                        # here.
                        dispatch_arms.append(BranchArm(
                            label=arm_label,
                            firstCallId=callee_entry_new,
                            empty=False,
                            terminus="continues",
                        ))
                    emit_edge(this_new_id, callee_entry_new, "invoke")
                    if is_new_continuation:
                        if callee_consumed:
                            continue
                        # The callee's ENTIRE recursively-inlined subtree
                        # never reached the continuation this call site
                        # gave it -- every visible path inside it dead-
                        # ended (a proven throw, a recursion cutoff, or
                        # itself hit this same fallback and still found
                        # nothing). Fall back to wiring the callee's OWN
                        # entry directly to that continuation, attributed
                        # to THIS call site (same convention as a normal
                        # return edge). Tagged "fallback" (not a proven
                        # edge -- this can't be fully disambiguated from a
                        # callee that genuinely, unconditionally never
                        # returns: a zero-call branch in the callee's real
                        # source is invisible to this call-projected CFG
                        # either way).
                        for cont in callee_continuations:
                            emit_edge(
                                callee_entry_new, cont, "sequence",
                                return_from=this_new_id, fallback=True,
                            )
                    else:
                        continuation_consumed = continuation_consumed or callee_consumed

                if dispatch_id is not None:
                    # convergesAt is deliberately left for
                    # _resolve_convergence, which treats this exactly like
                    # any other group: the implementations' return edges
                    # are what say where they rejoin, and those only exist
                    # once the whole trace is wired.
                    flat_groups.append(BranchGroup(
                        id=dispatch_id,
                        kind="DISPATCH",
                        method=nodes_by_id[original_id].callerMethod,
                        line=nodes_by_id[original_id].line,
                        arms=dispatch_arms,
                        branchPointIds=[this_new_id],
                        returnsTo=list(callee_continuations),
                    ))
            elif successors:
                for s in successors:
                    emit_edge(this_new_id, get_or_clone(s), "sequence")
                    if s not in walked:
                        stack.append(s)
            elif not nodes_by_id[original_id].deadEnd:
                for cont in continuations:
                    emit_edge(this_new_id, cont, "sequence", return_from=return_from)
                    continuation_consumed = True
                # else: throw-truncated dead end -- no edge emitted at
                # all, stays genuinely dead.

        # "data" edges are wired in a SEPARATE pass, after this instance's
        # own reachable body is fully walked -- NEVER by eagerly cloning a
        # data edge's target the way sequence/invoke targets are. A "data"
        # edge that turns out to reach outside this instance's own walked
        # node set (Joern's DDG overlay turning out to have a cross-method
        # edge, contrary to its intra-procedural characterization --
        # confirmed to actually occur, not hypothetical) would otherwise
        # mint a PHANTOM clone that's cloned but never reached via any
        # "sequence"/"invoke" edge -- reachable by nothing. Only wiring
        # data edges whose target is ALREADY in `local_clone` (i.e.
        # legitimately reached via this instance's own sequence/invoke
        # walk) means an out-of-scope data edge is just dropped -- a loss
        # of one signal in a rare case, not a dangling node.
        for original_id, this_new_id in local_clone.items():
            for data_target in data_out.get(original_id, []):
                if data_target in local_clone:
                    emit_edge(this_new_id, local_clone[data_target], "data")

        # This instance's own copy of its method's branch groups. The
        # suffix is the entry clone's -- unique per inline() call, so it
        # identifies the instance the way no single node id can (every
        # node gets its own counter value).
        for group in groups_by_method.get(method_name, ()):
            flat_groups.append(
                _scope_group_to_instance(group, suffix, local_clone, continuations)
            )

        # Final arm membership for every clone this instance made. Runs
        # unconditionally, not only when this method owns groups: a method
        # with no branches of its own still inherits the arms of the call
        # site that inlined it, which is the whole point of the propagation.
        #
        # A fresh list per node -- `clone` copies the field by reference, so
        # rewriting one in place would corrupt every sibling instance.
        for original_id, new_id in local_clone.items():
            tags = leaf_tags[new_id] if new_id in leaf_tags else tags_for(original_id)
            if tags:
                flat_nodes[new_id] = dataclasses.replace(
                    flat_nodes[new_id], branchArms=tags
                )

        return entry_new_id, continuation_consumed

    # Root has no outer continuation ([]) -- nothing to fall back to, so
    # its own "consumed" status is moot and discarded -- and sits at
    # nesting level 0, the origin every other depth counts up from.
    root_new_id, _ = inline(root_original_id, [], None, frozenset(), 0)

    nodes = list(flat_nodes.values())
    # A group with no `method` can't be attributed to an instance, so it
    # rides through unscoped rather than being dropped (only reachable
    # with a graph extracted before groups carried `method`).
    groups = _resolve_convergence(nodes, flat_edges, flat_groups, root_new_id)
    groups += [g for g in cfg.branchGroups if g.method is None]
    flat_edges = _annotate_branch_requirements(flat_edges, groups)

    return Graph(
        entryPoint=cfg.entryPoint,
        rootId=root_new_id,
        nodes=nodes,
        edges=flat_edges,
        branchGroups=groups,
    )
