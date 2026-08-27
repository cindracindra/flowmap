import dataclasses
import itertools
from dataclasses import dataclass

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
    MethodExitKind,
    Node,
    NodeSemanticFeatures,
)


@dataclass(frozen=True, slots=True)
class Continuation:
    """One route a completed call may resume.

    ``exitKind`` marks a method-local control exit rather than a concrete
    operation target. Such continuations are collapsed into ExitRoute values
    and never emitted as final graph nodes.
    """

    targetId: str
    returnFrom: str
    branchRequirements: tuple[BranchRequirement, ...] = ()
    exitKind: MethodExitKind | None = None


@dataclass(frozen=True, slots=True)
class ExitRoute:
    """A concrete active frontier leaving one inlined method instance."""

    sourceId: str
    kind: MethodExitKind
    branchRequirements: tuple[BranchRequirement, ...] = ()
    inferred: bool = False


@dataclass(frozen=True, slots=True)
class InlineResult:
    entryId: str
    exits: tuple[ExitRoute, ...] = ()


def _requirement_map(
    requirements: tuple[BranchRequirement, ...] | list[BranchRequirement],
) -> dict[str, str]:
    return {requirement.groupId: requirement.armLabel for requirement in requirements}


def _requirements_compatible(
    left: tuple[BranchRequirement, ...] | list[BranchRequirement],
    right: tuple[BranchRequirement, ...] | list[BranchRequirement],
) -> bool:
    left_by_group = _requirement_map(left)
    right_by_group = _requirement_map(right)
    return all(
        left_by_group[group_id] == right_by_group[group_id]
        for group_id in left_by_group.keys() & right_by_group.keys()
    )


def _merge_requirements(
    *sets: tuple[BranchRequirement, ...] | list[BranchRequirement],
) -> tuple[BranchRequirement, ...]:
    merged: dict[str, BranchRequirement] = {}
    for requirements in sets:
        for requirement in requirements:
            existing = merged.get(requirement.groupId)
            if existing is not None and existing.armLabel != requirement.armLabel:
                raise ValueError(
                    f"Conflicting arms for {requirement.groupId!r}: "
                    f"{existing.armLabel!r} and {requirement.armLabel!r}"
                )
            merged[requirement.groupId] = requirement
    return tuple(merged.values())


def flatten_cfg(cfg: Graph) -> Graph:
    """
    Inlines every internally-traversed callee at its own call site into
    one continuous trace rooted at cfg.entryPoint. Traversal returns typed
    path exits rather than deriving completion from whichever visible node
    happened to be last.

    Key construction choices:
      - A call site's own "sequence" edge to whatever follows it is
        replaced by a synthesized "sequence" edge from the callee's own
        tail(s), tagged returnFrom with the original call site's id (so
        a phase-tree builder can evaluate the real call-site pair, not
        the tail).
      - A throw reaches only a compatible catch continuation; otherwise it
        propagates outward as a genuine dead end.
      - Every method is cloned fresh per call site, never shared.
      - A tail call propagates its pending continuation through unchanged
        rather than minting a new one.
      - A method already being inlined higher up the same chain is cut
        off as a bare stub (recursion guard).
      - Explicit exit markers are authoritative. Legacy graphs without any
        exit marker retain one narrowly-scoped inferred fallback when their
        projected body exposes no normal completion route.
      - Each clone's `depth` is the invoke-nesting level it is created
        at, stamped from `inline`'s own recursion -- see `clone`.
      - Branch groups are cloned per instance too, `cs20` -> `cs20~7`,
        with every id inside them re-pointed at this instance's clones.
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
    seen_edges: set[
        tuple[str, str, str, str | None, tuple[tuple[str, str], ...]]
    ] = set()
    id_counter = itertools.count()

    def emit_edge(
        from_id: str,
        to_id: str,
        edge_type: str,
        return_from: str | None = None,
        fallback: bool = False,
        branch_requirements: tuple[BranchRequirement, ...] = (),
    ) -> None:
        requirement_key = tuple(
            (requirement.groupId, requirement.armLabel)
            for requirement in branch_requirements
        )
        key = (from_id, to_id, edge_type, return_from, requirement_key)
        if from_id == to_id or key in seen_edges:
            return
        seen_edges.add(key)
        flat_edges.append(
            Edge(
                source=from_id, target=to_id, type=edge_type,
                returnFrom=return_from, fallback=fallback,
                branchRequirements=list(branch_requirements),
            )
        )

    def clone(original_id: str, depth: int, *, materialize: bool = True) -> str:
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
        if materialize:
            flat_nodes[new_id] = dataclasses.replace(
                nodes_by_id[original_id], id=new_id, origId=original_id, depth=depth,
            )
        if materialize and original_id in cfg.semanticFeatures:
            # Feature records contain mutable lists, so clone through the
            # serialized shape rather than sharing them between instances.
            flat_semantic_features[new_id] = NodeSemanticFeatures.from_dict(
                cfg.semanticFeatures[original_id].to_dict()
            )
        return new_id

    def inline(
        entry_original_id: str,
        continuations: tuple[Continuation, ...],
        visited_methods: frozenset[str],
        depth: int,
        inherited_tags: tuple[BranchArmRef, ...] = (),
        inherited_loop_ids: tuple[str, ...] = (),
    ) -> InlineResult:
        """
        Clone and wire one method instance. Exit nodes are scoped control
        markers, not final operations: reaching one yields an ExitRoute whose
        source remains the deepest concrete active node.

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
                local_clone[original_id] = clone(
                    original_id,
                    at_depth,
                    materialize=nodes_by_id[original_id].type != "exit",
                )
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
        scoped_group_kinds = {
            f"{group.id}~{suffix}": group.kind
            for group in groups_by_method.get(method_name, ())
        }
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

        def requirements_for(original_id: str) -> tuple[BranchRequirement, ...]:
            return tuple(
                BranchRequirement(tag.groupId, tag.armLabel)
                for tag in tags_for(original_id)
            )

        def scoped_requirement(
            requirement: BranchRequirement,
        ) -> BranchRequirement:
            return BranchRequirement(
                f"{requirement.groupId}~{suffix}"
                if requirement.groupId in scoped_ids
                else requirement.groupId,
                requirement.armLabel,
            )

        def continuations_for(
            call_new_id: str, successors: list[str]
        ) -> tuple[Continuation, ...]:
            result: list[Continuation] = []
            for successor in successors:
                successor_new_id = get_or_clone(successor)
                successor_node = nodes_by_id[successor]
                result.append(Continuation(
                    targetId=successor_new_id,
                    returnFrom=call_new_id,
                    branchRequirements=requirements_for(successor),
                    exitKind=(
                        successor_node.exitKind
                        if successor_node.type == "exit"
                        else None
                    ),
                ))
            return tuple(result)

        method_exits: list[ExitRoute] = []
        # Extraction records ArmExit frontiers as call IDs, while a flattened
        # external/internal call completes at a leaf or deeper callee tail.
        # Retain that relationship so method exits can be qualified by the
        # authoritative arm route before the caller consumes them.
        completion_sources_by_frontier: dict[str, set[str]] = {}
        normal_completion_observed = False

        def add_exit(route: ExitRoute) -> None:
            if route not in method_exits:
                method_exits.append(route)

        def follow_continuations(
            route: ExitRoute,
            next_routes: tuple[Continuation, ...],
        ) -> bool:
            """Connect one callee exit to every compatible caller route.

            A continuation that is itself an exit is collapsed by carrying
            the same concrete source into this method's ExitRoute list.
            """
            nonlocal normal_completion_observed
            matched = False
            for continuation in next_routes:
                if not _requirements_compatible(
                    route.branchRequirements,
                    continuation.branchRequirements,
                ):
                    continue
                try_requirements = [
                    requirement
                    for requirement in continuation.branchRequirements
                    if scoped_group_kinds.get(requirement.groupId) == "TRY"
                ]
                if try_requirements:
                    # The innermost TRY requirement selects this immediate
                    # continuation. Outer requirements remain ordinary path
                    # constraints and are preserved by the merge below.
                    catches_throw = try_requirements[-1].armLabel != "noCatch"
                    if (route.kind == "throw") != catches_throw:
                        continue
                elif route.kind == "throw":
                    continue
                matched = True
                normal_completion_observed = True
                requirements = _merge_requirements(
                    route.branchRequirements,
                    continuation.branchRequirements,
                )
                if continuation.exitKind is not None:
                    add_exit(ExitRoute(
                        sourceId=route.sourceId,
                        kind=continuation.exitKind,
                        branchRequirements=requirements,
                        inferred=route.inferred,
                    ))
                else:
                    emit_edge(
                        route.sourceId,
                        continuation.targetId,
                        "sequence",
                        return_from=continuation.returnFrom,
                        fallback=route.inferred,
                        branch_requirements=requirements,
                    )
            if route.kind == "throw" and not matched:
                add_exit(route)
            return matched

        def follow_local_successors(
            source_id: str,
            source_requirements: tuple[BranchRequirement, ...],
            successors: list[str],
            *,
            return_from: str | None = None,
            inferred: bool = False,
        ) -> None:
            for successor in successors:
                target_id = get_or_clone(successor)
                target_node = nodes_by_id[successor]
                target_requirements = requirements_for(successor)
                if not _requirements_compatible(
                    source_requirements, target_requirements
                ):
                    continue
                requirements = _merge_requirements(
                    source_requirements, target_requirements
                )
                if target_node.type == "exit":
                    add_exit(ExitRoute(
                        sourceId=source_id,
                        kind=target_node.exitKind or "fallthrough",
                        branchRequirements=requirements,
                        inferred=inferred,
                    ))
                else:
                    emit_edge(
                        source_id,
                        target_id,
                        "sequence",
                        return_from=return_from,
                        fallback=inferred,
                        branch_requirements=requirements,
                    )

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
            return InlineResult(
                entryId=entry_new_id,
                exits=(ExitRoute(
                    sourceId=entry_new_id,
                    kind="fallthrough",
                    branchRequirements=requirements_for(entry_original_id),
                    inferred=True,
                ),),
            )

        deeper_visited = visited_methods | {method_name}
        walked: set[str] = set()
        stack: list[str] = [entry_original_id]
        while stack:
            original_id = stack.pop()
            if original_id in walked:
                continue
            walked.add(original_id)
            this_new_id = get_or_clone(original_id)
            original_node = nodes_by_id[original_id]

            if original_node.type == "exit":
                continue

            successors = sequence_out.get(original_id, [])
            invoke_targets = invoke_out.get(original_id, [])
            internal_targets = [
                t for t in invoke_targets if nodes_by_id[t].type == "entry"
            ]
            leaf_targets = [t for t in invoke_targets if nodes_by_id[t].type != "entry"]

            leaf_new_ids: list[str] = []
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
                leaf_new_ids.append(leaf_new_id)

            if leaf_new_ids:
                completion_sources_by_frontier.setdefault(original_id, set()).update(
                    leaf_new_ids
                )

            if internal_targets:
                local_continuations = continuations_for(this_new_id, successors)
                # A tail call does not create a new resume point. Its callee
                # completes directly into the continuation owned by this
                # method's caller, potentially crossing several tail frames.
                callee_continuations = local_continuations or continuations
                for successor in successors:
                    if nodes_by_id[successor].type != "exit" and successor not in walked:
                        stack.append(successor)
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
                    callee_result = inline(
                        target, callee_continuations,
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
                            firstCallId=callee_result.entryId,
                            empty=False,
                            terminus="continues",
                        ))
                    emit_edge(this_new_id, callee_result.entryId, "invoke")
                    completion_sources_by_frontier.setdefault(original_id, set()).update(
                        route.sourceId for route in callee_result.exits
                    )
                    for exit_route in callee_result.exits:
                        follow_continuations(exit_route, callee_continuations)

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
                        returnsTo=list(dict.fromkeys(
                            continuation.targetId
                            for continuation in callee_continuations
                            if continuation.exitKind is None
                        )),
                    ))
            else:
                frontiers = leaf_new_ids or [this_new_id]
                for frontier in frontiers:
                    follow_local_successors(
                        frontier,
                        requirements_for(original_id),
                        successors,
                        # A leaf is the concrete completion of the external
                        # call site. Crossing from it back into this method is
                        # a return boundary even though no body was inlined.
                        return_from=(this_new_id if leaf_new_ids else None),
                    )
                for successor in successors:
                    if nodes_by_id[successor].type != "exit" and successor not in walked:
                        stack.append(successor)

                # Compatibility for artifacts predating exit nodes. A real
                # exit marker always takes precedence; inference is reserved
                # for incomplete input and is explicitly marked fallback.
                if not successors and original_node.type != "entry":
                    legacy_kind: MethodExitKind = (
                        "throw" if original_node.deadEnd
                        else "return" if original_node.terminus == "return"
                        else "fallthrough"
                    )
                    for frontier in frontiers:
                        add_exit(ExitRoute(
                            sourceId=frontier,
                            kind=legacy_kind,
                            branchRequirements=requirements_for(original_id),
                            # A visible external leaf is itself a confirmed
                            # active frontier. Only a call-only terminal in
                            # an old artifact is genuinely unresolved.
                            inferred=not bool(leaf_new_ids),
                        ))

        # A CFG exit node after a branch is not lexically inside either arm.
        # Consequently the raw route that reaches it can be unqualified even
        # though its path is the branch's empty/continuing arm. Match concrete
        # completion sources back to authoritative ArmExit frontiers before
        # returning these routes to the caller; otherwise the caller emits an
        # unconditional return edge and later empty-arm routing can only add a
        # correctly-qualified duplicate beside it.
        authoritative_routes: list[
            tuple[MethodExitKind, set[str], tuple[BranchRequirement, ...]]
        ] = []
        for group in groups_by_method.get(method_name, ()):
            scoped_group_id = f"{group.id}~{suffix}"
            for arm in group.arms:
                own_requirement = BranchRequirement(scoped_group_id, arm.label)
                for exit_ in arm.exits:
                    frontier_ids = exit_.frontierIds or group.branchPointIds
                    concrete_sources = {
                        source
                        for frontier_id in frontier_ids
                        for source in completion_sources_by_frontier.get(
                            frontier_id,
                            ({local_clone[frontier_id]}
                             if frontier_id in local_clone
                             and local_clone[frontier_id] in flat_nodes
                             else set()),
                        )
                    }
                    if not concrete_sources:
                        continue
                    exit_requirements = _merge_requirements(
                        [scoped_requirement(requirement)
                         for requirement in exit_.branchRequirements],
                        [own_requirement],
                    )
                    authoritative_routes.append((
                        "fallthrough" if exit_.kind == "continues" else exit_.kind,
                        concrete_sources,
                        exit_requirements,
                    ))

        qualified_method_exits: list[ExitRoute] = []
        for route in method_exits:
            matches = [
                exit_requirements
                for kind, sources, exit_requirements in authoritative_routes
                # ``fallthrough`` here represents ArmExit(kind="continues"):
                # it describes leaving the arm, not necessarily leaving the
                # method. Common code after the branch may subsequently reach
                # an explicit return, so the same frontier can legitimately
                # produce a method-level ``return``. It cannot produce throw.
                if (kind == route.kind or (kind == "fallthrough" and route.kind != "throw"))
                and route.sourceId in sources
                and _requirements_compatible(
                    route.branchRequirements, exit_requirements
                )
            ]
            candidates = (
                [dataclasses.replace(
                    route,
                    branchRequirements=_merge_requirements(
                        route.branchRequirements, exit_requirements
                    ),
                ) for exit_requirements in matches]
                if matches else [route]
            )
            for candidate in candidates:
                if candidate not in qualified_method_exits:
                    qualified_method_exits.append(candidate)
        method_exits = qualified_method_exits

        # Compatibility for pre-exit artifacts only. If their projected body
        # exposes no normal route at all, the old format cannot distinguish
        # "all paths throw" from a filtered zero-call continuing path. Keep
        # that uncertainty explicit as one inferred entry fallback. A graph
        # containing any exit marker never uses this recovery path.
        has_authoritative_exit = any(
            nodes_by_id[original_id].type == "exit"
            for original_id in local_clone
        )
        if (
            continuations
            and not has_authoritative_exit
            and not normal_completion_observed
            and not any(route.kind != "throw" for route in method_exits)
        ):
            add_exit(ExitRoute(
                sourceId=entry_new_id,
                kind="fallthrough",
                branchRequirements=requirements_for(entry_original_id),
                inferred=True,
            ))

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
                if (
                    data_target in local_clone
                    and this_new_id in flat_nodes
                    and local_clone[data_target] in flat_nodes
                ):
                    emit_edge(this_new_id, local_clone[data_target], "data")

        # This instance's own copy of its method's branch groups. The
        # suffix is the entry clone's -- unique per inline() call, so it
        # identifies the instance the way no single node id can (every
        # node gets its own counter value).
        for group in groups_by_method.get(method_name, ()):
            arms: list[BranchArm] = []
            for arm in group.arms:
                own_requirement = BranchRequirement(
                    f"{group.id}~{suffix}", arm.label
                )
                scoped_exits = []
                for exit_ in arm.exits:
                    exit_requirements = _merge_requirements(
                        [scoped_requirement(requirement)
                         for requirement in exit_.branchRequirements],
                        [own_requirement],
                    )
                    route_kind: MethodExitKind = (
                        "fallthrough" if exit_.kind == "continues" else exit_.kind
                    )
                    resolved_routes = [
                        route for route in method_exits
                        if route.kind == route_kind
                        and _requirements_compatible(
                            route.branchRequirements, exit_requirements
                        )
                        and own_requirement in route.branchRequirements
                    ]
                    if resolved_routes:
                        # Keep each route intact. Combining its source with
                        # another route's requirements or targets would
                        # recreate the invalid many-to-many cross product
                        # that authoritative exits are intended to prevent.
                        for route in resolved_routes:
                            route_requirements = _merge_requirements(
                                route.branchRequirements, exit_requirements
                            )
                            targets = [] if route.kind == "throw" else [
                                continuation.targetId
                                for continuation in continuations
                                if continuation.exitKind is None
                                and _requirements_compatible(
                                    route_requirements,
                                    continuation.branchRequirements,
                                )
                            ]
                            scoped_exits.append(dataclasses.replace(
                                exit_,
                                frontierIds=[route.sourceId],
                                targetIds=list(dict.fromkeys(targets)),
                                branchRequirements=list(route_requirements),
                            ))
                    else:
                        # A continuing arm may end at an in-method merge and
                        # therefore not appear in this method's exit routes.
                        # Preserve its scoped extraction frontier for routing
                        # to resolve after the complete flat graph exists.
                        scoped_exits.append(dataclasses.replace(
                            exit_,
                            frontierIds=[
                                local_clone[frontier]
                                for frontier in exit_.frontierIds
                                if frontier in local_clone
                                and local_clone[frontier] in flat_nodes
                            ],
                            targetIds=[],
                            branchRequirements=list(exit_requirements),
                        ))
                arms.append(dataclasses.replace(
                    arm,
                    firstCallId=(
                        local_clone.get(arm.firstCallId)
                        if arm.firstCallId else None
                    ),
                    empty=(
                        local_clone.get(arm.firstCallId) not in flat_nodes
                        if arm.firstCallId else True
                    ),
                    exits=scoped_exits,
                    targetIds=None,
                ))
            flat_groups.append(dataclasses.replace(
                group,
                id=f"{group.id}~{suffix}",
                arms=arms,
                branchPointIds=[
                    local_clone[point]
                    for point in group.branchPointIds
                    if point in local_clone and local_clone[point] in flat_nodes
                ],
                returnsTo=list(dict.fromkeys(
                    continuation.targetId
                    for continuation in continuations
                    if continuation.exitKind is None
                )),
                convergesAt=None,
            ))

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
            if new_id in flat_nodes and (tags or loop_ids):
                flat_nodes[new_id] = dataclasses.replace(
                    flat_nodes[new_id], branchArms=tags, loopIds=loop_ids
                )

        return InlineResult(entryId=entry_new_id, exits=tuple(method_exits))

    # Root has no outer continuation ([]) -- nothing to fall back to, so
    # its own "consumed" status is moot and discarded -- and sits at
    # nesting level 0, the origin every other depth counts up from.
    root_result = inline(root_original_id, (), frozenset(), 0)
    root_new_id = root_result.entryId

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
            flat_semantic_features, set(flat_nodes)
        ),
    )
