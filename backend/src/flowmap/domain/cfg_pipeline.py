import dataclasses
import itertools
from collections import deque

from model import Edge, Graph, Node
from domain.util import is_noise, is_jdk_call_site_strip


def classify_roots_and_orphans(graph: Graph) -> Graph:
    """
    Returns a new Graph with `roots`/`orphans` populated from `graph`'s own
    "invoke" edges.
    """

    nodes_by_id = {n.id: n for n in graph.nodes}
    entry_id_by_fullname = {
        n.calleeFullName: n.id for n in graph.nodes if n.type == "entry"
    }

    invoke_in: dict[str, int] = {}
    entries_with_outgoing_calls: set[str] = set()
    for e in graph.edges:
        if e.type != "invoke":
            continue
        invoke_in[e.target] = invoke_in.get(e.target, 0) + 1
        call_node = nodes_by_id.get(e.source)
        if call_node is None or call_node.callerMethod is None:
            continue
        entry_id = entry_id_by_fullname.get(call_node.callerMethod)
        if entry_id is not None:
            entries_with_outgoing_calls.add(entry_id)

    entry_ids = [n.id for n in graph.nodes if n.type == "entry"]
    roots = sorted(
        id_
        for id_ in entry_ids
        if invoke_in.get(id_, 0) == 0 and id_ in entries_with_outgoing_calls
    )
    orphans = sorted(
        id_
        for id_ in entry_ids
        if invoke_in.get(id_, 0) == 0 and id_ not in entries_with_outgoing_calls
    )

    return dataclasses.replace(graph, roots=roots, orphans=orphans)


def find_roots_above(graph: Graph, anchor_id: str) -> list[str]:
    """
    Walks "invoke" edges backward from an "entry" node (anchor_id) to
    find every true root -- an entry node with no caller -- that can
    reach it.
    """
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
            return  # cycle -- nothing further up along this path
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
    """
    Extracts the forward-reachable subgraph from root_id (an "entry"
    node id) out of the full-codebase graph -- the same
    {"entryPoint", "nodes", "edges"} shape a single-entry-point Joern
    extraction produces, just computed here from the already-built full
    graph instead of a fresh query. Drops straight into
    filter_intermethod_cfg -> flatten_intermethod_cfg -> build_phase_tree
    unchanged.

    "sequence"/"invoke" edges drive reachability; "data" edges never do
    (matching full_cfg.sc's own extraction, where a data edge only ever
    connects two call nodes that are already reachable via
    sequence/invoke on their own) -- included in the output only when
    both endpoints are already reached, never used to pull in a node
    that wouldn't otherwise be part of the slice.
    """
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
    return Graph(entryPoint=root.calleeFullName, nodes=sliced_nodes, edges=sliced_edges)


def slice_anchored_cfg(graph: Graph, method_full_name: str) -> list[Graph]:
    """
    The whole chain(s) the method named method_full_name participates in,
    each independently feedable to filter_intermethod_cfg ->
    flatten_intermethod_cfg -> build_phase_tree as its own coherent unit --
    see find_roots_above's docstring for why this can be more than one
    Graph (a shared anchor with multiple independent top-level callers has
    multiple disjoint chains, not one).
    """
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
) -> list[str]:
    """
    Follows outgoing edges of ONE type starting at node_id, through any
    run of excluded nodes, until reaching kept node(s) -- or nothing, if
    the chain dead-ends entirely inside excluded territory (or cycles
    without ever reaching a kept node).

    Returned as an ORDER-PRESERVING list, not a set: when an excluded run
    fans out to more than one surviving descendant (e.g. a noise
    if-condition with two real branches), the caller's emitted edge order
    should still reflect the original left-to-right branch order, not an
    arbitrary set-iteration order. Duplicates are fine here -- deduped by
    the caller (_bridge_edges) while preserving first occurrence.
    """
    if node_id not in excluded_ids:
        return [node_id]
    if node_id in memo:
        return memo[node_id]
    if node_id in visiting:
        return []  # cycle entirely within excluded nodes -- nothing to bridge to

    resolved: list[str] = []
    for successor in adjacency_out.get(node_id, []):
        resolved.extend(
            _resolve_kept_targets(successor, excluded_ids, adjacency_out, memo, visiting | {node_id})
        )
    memo[node_id] = resolved
    return resolved


def _bridge_edges(typed_edges: list[Edge], excluded_ids: set[str]) -> list[Edge]:
    """
    Rebuilds ONE edge type's edges with excluded nodes spliced out: for
    every surviving edge whose source is a kept node, its target is
    resolved past any run of excluded nodes to the nearest kept
    descendant(s) (see _resolve_kept_targets), so A -> B -> C collapses
    to A -> C when B is excluded, instead of leaving a dangling A -> B
    with C unreachable. Edges whose source is itself excluded aren't
    re-emitted directly -- they're already folded into the resolution for
    whatever kept node points at that excluded source.
    """
    adjacency_out = _adjacency_out(typed_edges)

    memo: dict[str, list[str]] = {}
    seen_pairs: set[tuple[str, str]] = set()
    bridged: list[Edge] = []
    for edge in typed_edges:
        if edge.source in excluded_ids:
            continue
        for target in _resolve_kept_targets(edge.target, excluded_ids, adjacency_out, memo, frozenset()):
            pair = (edge.source, target)
            if pair in seen_pairs or pair[0] == pair[1]:
                continue
            seen_pairs.add(pair)
            bridged.append(Edge(source=pair[0], target=pair[1], type=edge.type))
    return bridged


def _migrate_branch_tags(
    all_nodes: list[Node], kept_nodes: list[Node], sequence_edges: list[Edge], excluded_ids: set[str]
) -> list[Node]:
    """
    A node carrying branchGroupId/armLabel (full_cfg.sc's emitBranchGroup
    tags a branch arm's first call) can itself be excluded as noise here
    -- e.g. `result = service.fetch();` as an arm's first statement:
    Joern's first call in that arm's AST subtree is `<operator>.assignment`
    (the real call is nested inside it as an argument), so that's the node
    Scala tags, and it's also exactly the shape filter_noise_cfg strips.
    Bridging already reconnects the EDGES around a gap like this (see
    _bridge_edges); this migrates the NODE-level tag the same way, onto
    the nearest surviving "sequence" descendant -- same reasoning
    DESIGN.md §2.5 documents for divergence evidence migrating onto the
    nearest surviving predecessor when its own node is excluded, just
    forward instead of backward and for a node tag instead of edge
    fan-out.

    Never overwrites a node that already carries its OWN branchGroupId --
    only a tag with nowhere left to live (its origin node was excluded)
    gets moved. If an excluded node's resolution fans out to more than one
    surviving descendant (possible in principle, not observed in any real
    example so far), or if two different excluded, differently-tagged
    nodes resolve to the SAME survivor, the tag is deliberately left off
    rather than guessing which one should win.
    """
    tagged_excluded = {
        n.id: (n.branchGroupId, n.armLabel)
        for n in all_nodes
        if n.id in excluded_ids and n.branchGroupId is not None
    }
    if not tagged_excluded:
        return kept_nodes

    adjacency_out = _adjacency_out(sequence_edges)
    memo: dict[str, list[str]] = {}
    migrated: dict[str, tuple[str, str]] = {}
    conflicted: set[str] = set()
    for excluded_id, tag in tagged_excluded.items():
        for target_id in _resolve_kept_targets(excluded_id, excluded_ids, adjacency_out, memo, frozenset()):
            if target_id in migrated and migrated[target_id] != tag:
                conflicted.add(target_id)
                continue
            migrated[target_id] = tag
    for target_id in conflicted:
        migrated.pop(target_id, None)

    return [
        dataclasses.replace(n, branchGroupId=migrated[n.id][0], armLabel=migrated[n.id][1])
        if n.id in migrated and n.branchGroupId is None
        else n
        for n in kept_nodes
    ]


def _tag_dead_ends(nodes: list[Node]) -> list[Node]:
    """
    deadEnd=True iff this node's own `terminus` (set at extraction time by
    full_cfg.sc's classifyTerminus -- see node.py) is "throw": ground
    truth from the CFG walk itself. Previously reconstructed from a
    before/after bridging diff (whether a node lost its sole sequence
    successor once noise nodes were stripped) -- replaced because that
    diff couldn't tell a proven throw apart from an ordinary tail call
    whose successor happened to be a noise-stripped node with nothing
    behind it (e.g. `e.getMessage()` before `System.out.println(...)`,
    itself stripped as java.io. noise -- confirmed tagged deadEnd=True
    under the old reconstruction despite DESIGN.md's own §8-area
    description of that node as "a pure return", not a throw). See the
    2026-08-10 session-log entry, §0, for the full trace of that bug and
    why every deadEnd consumer (phase_discovery.py's diverges/converges/
    visit R4, cfg_pipeline.py's inline()) only ever wanted the throw-only
    meaning in the first place.

    A node whose JSON predates this field (terminus absent) simply gets
    deadEnd=None here, same as before this field existed -- no crash, just
    a degraded (non-)signal until the graph is regenerated.
    """
    return [
        dataclasses.replace(n, deadEnd=True) if n.terminus == "throw" else n
        for n in nodes
    ]


def filter_noise_cfg(cfg: Graph) -> Graph:
    """
    Drops noise/JDK-bookkeeping "call" nodes, bridging around each gap so
    the surrounding flow stays connected (e.g. A -> B -> C with B
    excluded becomes A -> C). Also tags each surviving node whose
    terminus is a proven throw with deadEnd=True -- see PHASING_RULES.md
    for why phaser needs this distinguished from an ordinary excluded
    node, and `_tag_dead_ends` above for where that fact comes from --
    and migrates any branchGroupId/armLabel tag whose own node got
    excluded onto the nearest surviving descendant (`_migrate_branch_tags`
    above).

    Matches against each node's calleeFullName (its resolved target),
    never code (its source text) -- code was tried first and found
    unreliable.
    """
    excluded_ids = {
        n.id for n in cfg.nodes
        if n.type == "call"
        and n.calleeFullName is not None
        and (is_noise(n.calleeFullName) or is_jdk_call_site_strip(n.calleeFullName))
    }
    if not excluded_ids:
        return dataclasses.replace(cfg, nodes=_tag_dead_ends(cfg.nodes))

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
    sequence_typed_edges: list[Edge] = []
    for edge_type in edge_types:
        typed_edges = [e for e in cfg.edges if e.type == edge_type]
        if edge_type == "sequence":
            typed_edges = [e for e in typed_edges if e.source not in throw_ids]
            sequence_typed_edges = typed_edges
        kept_edges.extend(_bridge_edges(typed_edges, excluded_ids))

    kept_nodes = _migrate_branch_tags(cfg.nodes, kept_nodes, sequence_typed_edges, excluded_ids)

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
    kept_nodes = [
        n for n in kept_nodes
        if n.id in connected_ids or n.id in root_ids
    ]

    return dataclasses.replace(cfg, nodes=kept_nodes, edges=kept_edges)


def _compute_depths(cfg: Graph, root_id: str) -> dict[str, int]:
    """
    0-1 BFS over `cfg`'s own "sequence" (cost 0) and "invoke" (cost 1)
    edges, keyed by ORIGINAL (pre-clone) node id. Deliberately run against
    the PRE-flatten graph, not the flattened one: a flattened graph's
    "sequence" edges include fallback/returnFrom-attributed ones with no
    edge-type-based cost that gives the right depth for what crossing them
    means (a "return" conceptually pops back down, not "stay at the same
    level") -- DESIGN.md's "Sixth bug"/"Seventh bug" (§8) already found
    and fixed this once on the old Python visualizer; frontend/src/lib/
    layout.ts's own `computeDepths` reintroduced it by recomputing fresh
    on the flattened graph instead of inheriting this. Mirrors
    layout.ts's algorithm exactly (same reasoning, just the Python side of
    it): 0-cost edges pushed to the front of the deque so they're
    processed before any 1-cost alternative reaches the same node first.
    """
    adjacency: dict[str, list[tuple[str, int]]] = {}
    for e in cfg.edges:
        if e.type not in ("sequence", "invoke"):
            continue
        cost = 1 if e.type == "invoke" else 0
        adjacency.setdefault(e.source, []).append((e.target, cost))

    depths: dict[str, int] = {root_id: 0}
    frontier: deque[str] = deque([root_id])
    while frontier:
        node_id = frontier.popleft()
        node_depth = depths[node_id]
        for target, cost in adjacency.get(node_id, []):
            candidate = node_depth + cost
            if target not in depths or candidate < depths[target]:
                depths[target] = candidate
                if cost == 0:
                    frontier.appendleft(target)
                else:
                    frontier.append(target)
    return depths


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
      - Each clone's `depth` is looked up by origId against depths
        computed once, up front, on the PRE-flatten `cfg` -- see
        `_compute_depths`'s docstring for why this can't be recomputed
        correctly on the flattened graph's own edges.
    """
    nodes_by_id = {n.id: n for n in cfg.nodes}

    root_original_id = next(
        n.id for n in cfg.nodes if n.type == "entry" and n.calleeFullName == cfg.entryPoint
    )
    # Computed once, up front, against the PRE-flatten graph -- see
    # _compute_depths's docstring for why depth can't be recomputed
    # correctly on the flattened graph's own edges instead.
    depths_by_original_id = _compute_depths(cfg, root_original_id)

    sequence_out: dict[str, list[str]] = {}
    invoke_out: dict[str, list[str]] = {}
    data_out: dict[str, list[str]] = {}
    for e in cfg.edges:
        bucket = {"sequence": sequence_out, "invoke": invoke_out, "data": data_out}.get(e.type)
        if bucket is not None:
            bucket.setdefault(e.source, []).append(e.target)

    flat_nodes: dict[str, Node] = {}
    flat_edges: list[Edge] = []
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

    def clone(original_id: str) -> str:
        new_id = f"{original_id}~{next(id_counter)}"
        flat_nodes[new_id] = dataclasses.replace(
            nodes_by_id[original_id], id=new_id, origId=original_id,
            depth=depths_by_original_id.get(original_id),
        )
        return new_id

    def inline(
        entry_original_id: str,
        continuations: list[str],
        return_from: str | None,
        visited_methods: frozenset[str],
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
        """
        local_clone: dict[str, str] = {}

        def get_or_clone(original_id: str) -> str:
            if original_id not in local_clone:
                local_clone[original_id] = clone(original_id)
            return local_clone[original_id]

        entry_new_id = get_or_clone(entry_original_id)
        method_name = nodes_by_id[entry_original_id].calleeFullName
        if method_name in visited_methods:
            return entry_new_id, False

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
                emit_edge(this_new_id, get_or_clone(leaf_target), "invoke")

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
                for target in internal_targets:
                    callee_entry_new, callee_consumed = inline(
                        target, callee_continuations, callee_return_from, deeper_visited
                    )
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

        return entry_new_id, continuation_consumed

    # root_original_id/depths_by_original_id were computed at the top of
    # this function, before `clone` needed them.
    # Root has no outer continuation ([]) -- nothing to fall back to, so
    # its own "consumed" status is moot and discarded.
    root_new_id, _ = inline(root_original_id, [], None, frozenset())

    return Graph(
        entryPoint=cfg.entryPoint,
        rootId=root_new_id,
        nodes=list(flat_nodes.values()),
        edges=flat_edges,
        # Method-level metadata, untouched by flattening -- see
        # branch.py/graph.py.
        branchGroups=cfg.branchGroups,
    )

