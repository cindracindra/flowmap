import dataclasses
import itertools

from domain.cfg_loops import scope_loop_to_instance, tag_loop_back_edges
from domain.cfg_routing import analyze_branch_routes, materialize_empty_arm_routes
from domain.cfg_semantics import scoped_semantic_features
from model import (
    BranchArm,
    BranchArmRef,
    BranchGroup,
    BranchRequirement,
    Edge,
    Graph,
    LoopGroup,
    Node,
    NodeSemanticFeatures,
)


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
        # for the zero-call-arm case in _analyze_branch_routes.
        returnsTo=list(continuations),
        convergesAt=None,  # needs the whole trace -- see _analyze_branch_routes
    )


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

    loops_by_method: dict[str, list[LoopGroup]] = {}
    for loop in cfg.loopGroups:
        if loop.method is not None:
            loops_by_method.setdefault(loop.method, []).append(loop)

    flat_nodes: dict[str, Node] = {}
    flat_semantic_features: dict[str, NodeSemanticFeatures] = {}
    flat_edges: list[Edge] = []
    flat_groups: list[BranchGroup] = []
    flat_loops: list[LoopGroup] = []
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
        if original_id in cfg.semanticFeatures:
            # Feature records contain mutable lists, so clone through the
            # serialized shape rather than sharing them between instances.
            flat_semantic_features[new_id] = NodeSemanticFeatures.from_dict(
                cfg.semanticFeatures[original_id].to_dict()
            )
        return new_id

    def inline(
        entry_original_id: str,
        continuations: list[str],
        return_from: str | None,
        visited_methods: frozenset[str],
        depth: int,
        inherited_tags: tuple[BranchArmRef, ...] = (),
        inherited_loop_ids: tuple[str, ...] = (),
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
        scoped_loop_ids = {loop.id for loop in loops_by_method.get(method_name, ())}

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

        def loops_for(original_id: str) -> list[str]:
            own = (
                f"{loop_id}~{suffix}" if loop_id in scoped_loop_ids else loop_id
                for loop_id in nodes_by_id[original_id].loopIds
            )
            return list(dict.fromkeys((*inherited_loop_ids, *own)))

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
            if inherited_loop_ids:
                flat_nodes[entry_new_id] = dataclasses.replace(
                    flat_nodes[entry_new_id], loopIds=loops_for(entry_original_id)
                )
            for continuation in continuations:
                emit_edge(
                    entry_new_id, continuation, "sequence",
                    return_from=return_from, fallback=True,
                )
            return entry_new_id, bool(continuations)

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
                # still nests -- so it deepens exactly like an internal one,
                # just without recursing. Clone it per invoking call site:
                # two source calls to the same external method are two
                # operation occurrences and must not visually converge on a
                # shared leaf merely because extraction reused its target.
                leaf_new_id = clone(leaf_target, depth + 1)
                leaf_tags = tags_for(original_id)
                leaf_loops = loops_for(original_id)
                if leaf_tags or leaf_loops:
                    flat_nodes[leaf_new_id] = dataclasses.replace(
                        flat_nodes[leaf_new_id],
                        branchArms=leaf_tags,
                        loopIds=leaf_loops,
                    )
                emit_edge(this_new_id, leaf_new_id, "invoke")

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
                        inherited_loop_ids=tuple(loops_for(original_id)),
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
                    # _analyze_branch_routes, which treats this exactly like
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

        for loop in loops_by_method.get(method_name, ()):
            flat_loops.append(scope_loop_to_instance(loop, suffix))

        # Final arm membership for every clone this instance made. Runs
        # unconditionally, not only when this method owns groups: a method
        # with no branches of its own still inherits the arms of the call
        # site that inlined it, which is the whole point of the propagation.
        #
        # A fresh list per node -- `clone` copies the field by reference, so
        # rewriting one in place would corrupt every sibling instance.
        for original_id, new_id in local_clone.items():
            tags = tags_for(original_id)
            loop_ids = loops_for(original_id)
            if tags or loop_ids:
                flat_nodes[new_id] = dataclasses.replace(
                    flat_nodes[new_id], branchArms=tags, loopIds=loop_ids
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
    analysis_groups = flat_groups + [
        group for group in cfg.branchGroups if group.method is None
    ]
    groups, flat_edges = analyze_branch_routes(
        nodes, flat_edges, analysis_groups, root_new_id
    )
    flat_edges = materialize_empty_arm_routes(nodes, flat_edges, groups)
    flat_edges = tag_loop_back_edges(nodes, flat_edges, root_new_id)

    return Graph(
        entryPoint=cfg.entryPoint,
        rootId=root_new_id,
        nodes=nodes,
        edges=flat_edges,
        branchGroups=groups,
        loopGroups=flat_loops + [loop for loop in cfg.loopGroups if loop.method is None],
        semanticFeatures=scoped_semantic_features(
            flat_semantic_features, set(flat_nodes), flat_edges
        ),
    )
