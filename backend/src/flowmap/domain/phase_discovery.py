from __future__ import annotations

from dataclasses import asdict
import networkx as nx

from model import Graph, Phase, Transition


def split_graph(graph: Graph) -> dict[str, nx.DiGraph]:
    """
    Splits a Graph into three typed DiGraphs -- "sequence" (intra-method
    order), "invoke" (interprocedural), "data" (DDG).
    """
    graphs = {name: nx.DiGraph() for name in ("sequence", "invoke", "data")}
    for node in graph.nodes:
        attrs = asdict(node)
        if node.type in ("entry", "call"):
            graphs["sequence"].add_node(node.id, **attrs)
        if node.type in ("call", "entry", "leaf"):
            graphs["invoke"].add_node(node.id, **attrs)
        if node.type == "call":
            graphs["data"].add_node(node.id, **attrs)
    for edge in graph.edges:
        graphs[edge.type].add_edge(edge.source, edge.target, returnFrom=edge.returnFrom)
    return graphs


def compute_back_edges(
    flow_graph: nx.DiGraph, root_id: str
) -> frozenset[tuple[str, str]]:
    """
    Loop back-edges via dominance: edge (u, v) is a back-edge iff v dominates u
    -- standard compiler-theory loop detection, not a heuristic.
    """
    idom = nx.immediate_dominators(flow_graph, root_id)

    def dominates(v: str, u: str) -> bool:
        node, seen = u, set()
        while True:
            if node == v:
                return True
            if node in seen:
                return False  # defensive -- shouldn't happen in a valid dominator tree
            seen.add(node)
            parent = idom.get(node)
            if parent is None or parent == node:
                return False
            node = parent

    return frozenset((u, v) for u, v in flow_graph.edges() if dominates(v, u))


def diverges(node_id: str, sequence_graph: nx.DiGraph) -> bool:
    """
    Real fork: more than one LIVE (non-dead-end) successor.
    """
    if node_id not in sequence_graph:
        return False
    live = [
        s
        for s in sequence_graph.successors(node_id)
        if not sequence_graph.nodes[s].get("deadEnd")
    ]
    return len(live) > 1


def converges(
    node_id: str, sequence_graph: nx.DiGraph, back_edges: frozenset[tuple[str, str]]
) -> bool:
    """
    Real merge: more than one incoming edge, excluding loop back-edges.
    """
    if node_id not in sequence_graph:
        return False
    return (
        sum(1 for u, v in sequence_graph.in_edges(node_id) if (u, v) not in back_edges)
        > 1
    )


def shares_data(data_graph: nx.DiGraph, call_a: str, call_b: str) -> bool | None:
    """
    None (not False) when neither call has any data edges at all --
    "nothing to compare" is a different claim from "compared and found
    unrelated".
    """
    if call_a not in data_graph or call_b not in data_graph:
        return None
    if data_graph.degree(call_a) == 0 and data_graph.degree(call_b) == 0:
        return None
    return data_graph.has_edge(call_a, call_b) or data_graph.has_edge(call_b, call_a)


def _receiver_class(callee_full_name: str) -> str:
    """'pkg.Outer.method:retType(params)' -> 'pkg.Outer', best-effort."""
    signature_free = callee_full_name.split(":", 1)[0]
    return signature_free.rsplit(".", 1)[0] if "." in signature_free else signature_free


def shares_receiver_class(sequence_graph: nx.DiGraph, call_a: str, call_b: str) -> bool:
    """Same target class -- cheapest, terminal fallback."""
    return _receiver_class(
        sequence_graph.nodes[call_a]["calleeFullName"]
    ) == _receiver_class(sequence_graph.nodes[call_b]["calleeFullName"])


def same_callee(sequence_graph: nx.DiGraph, call_a: str, call_b: str) -> bool:
    """
    Same exact method, not just same class -- overrides a Level 2 "data-
    unrelated" verdict for strictly adjacent calls (never "anywhere in the
    method"). See DESIGN.md §2.7 for the motivating example.
    """
    return (
        sequence_graph.nodes[call_a]["calleeFullName"]
        == sequence_graph.nodes[call_b]["calleeFullName"]
    )


def _flow_graph(sequence_graph: nx.DiGraph, invoke_graph: nx.DiGraph) -> nx.DiGraph:
    """
    Sequence edges plus invoke edges into internally-traversed entries,
    for dominance/reachability purposes only -- diverges/converges
    themselves still only ever consult sequence_graph, so invoke-edge
    multiplicity (polymorphism) never reads as a gate.
    """
    flow = sequence_graph.copy()
    for u, v in invoke_graph.edges():
        if invoke_graph.nodes[v]["type"] == "entry":
            flow.add_edge(u, v)
    return flow


def _is_gate(
    real_predecessor: str,
    node_id: str,
    sequence_graph: nx.DiGraph,
    back_edges: frozenset[tuple[str, str]],
) -> bool:
    """Level 1: does the edge INTO node_id carry real structural evidence
    -- the predecessor forking, or node_id itself being a genuine merge
    point? Evaluated on the real endpoints, per PHASING_RULES.md R7."""
    return diverges(real_predecessor, sequence_graph) or converges(
        node_id, sequence_graph, back_edges
    )


def _evaluate(
    subject_id: str,
    node_id: str,
    sequence_graph: nx.DiGraph,
    data_graph: nx.DiGraph,
    gate: bool,
) -> tuple[Transition, bool]:
    """One Level 1/2/3 verdict for subject_id -> node_id. Returns
    (transition, merges). `gate` is passed in rather than recomputed here
    since callers evaluate it against different node pairs (real adjacency
    vs. the R7-substituted subject)."""
    if gate:
        return Transition(subject_id, "gate", 1), False
    data_verdict = shares_data(data_graph, subject_id, node_id)
    if data_verdict is True:
        return Transition(subject_id, "data-related", 2), True
    if data_verdict is False and same_callee(sequence_graph, subject_id, node_id):
        return Transition(subject_id, "same-callee-related", 2), True
    if data_verdict is False:
        return Transition(subject_id, "data-unrelated", 2), False
    if shares_receiver_class(sequence_graph, subject_id, node_id):
        return Transition(subject_id, "class-related", 3), True
    return Transition(subject_id, "class-unrelated", 3), False


def _return_edges_by_call_site(graph: Graph) -> dict[str, list[tuple[str, str]]]:
    """
    Every synthesized "sequence" edge tagged with a returnFrom (a return or
    fallback edge from processor.flatten_intermethod_cfg), bucketed by the
    call site it's attributed to -- a resolved callee has no other way to
    tell its caller where to resume (PHASING_RULES.md R7).
    """
    buckets: dict[str, list[tuple[str, str]]] = {}
    for edge in graph.edges:
        if edge.type == "sequence" and edge.returnFrom is not None:
            buckets.setdefault(edge.returnFrom, []).append((edge.source, edge.target))
    return buckets


def _resolve_phases(
    entry_id: str,
    sequence_graph: nx.DiGraph,
    invoke_graph: nx.DiGraph,
    data_graph: nx.DiGraph,
    back_edges: frozenset[tuple[str, str]],
    return_edges_by_call_site: dict[str, list[tuple[str, str]]],
) -> list[Phase]:
    """
    Resolves ONE internally-traversed method's own phase list, starting
    just past its entry node -- see PHASING_RULES.md R1/R2/R8. A "sequence"
    edge tagged with returnFrom belongs to a DIFFERENT method (it's some
    other call's own exit back to ITS caller), so it's never followed here
    -- only plain edges and "invoke" edges into further internally-
    traversed entries count as this method's own body.

    Recurses into `_resolve_phases` for every call site that invokes
    further internal work (R8); that call's own resolved phases are
    spliced directly into this method's list in place of the call itself
    (R5/R6) rather than kept as a separate member. Recursion is bounded by
    call-nesting depth (mirrors processor.flatten_intermethod_cfg.inline,
    which recurses the same way), not by how long any one method's body
    is.
    """
    phases: list[Phase] = []
    phase_of: dict[str, int] = {}
    visited: set[str] = set()

    def open_phase(node_id: str, opened_by: Transition | None) -> int:
        phase_of[node_id] = len(phases)
        phases.append(Phase(nodes=[node_id], opened_by=opened_by))
        return phase_of[node_id]

    def append(node_id: str, phase_id: int, transition: Transition) -> None:
        phase_of[node_id] = phase_id
        phases[phase_id].nodes.append(node_id)
        phases[phase_id].transitions.append(transition)

    def splice(
        real_predecessor: str | None,
        subject: str | None,
        call_site_id: str,
        callee_phases: list[Phase],
    ) -> int | None:
        """R5/R6: call_site_id invokes further internal work and is never
        itself a member -- its callee's resolved phases are spliced in
        directly. The first is evaluated against whatever preceded the
        call: Level 1 (gate) on the REAL nodes on each side -- real_predecessor,
        and call_site_id itself, since that is the node the flattened
        graph's return/fallback edge actually lands on (the callee's own
        internal content is never directly reachable from outside it).
        Level 2/3 on the two ORIGINAL call sites (subject, call_site_id) --
        never on call_site_id's own callee, which is exactly the parent-
        child mix-up R1 exists to rule out (PHASING_RULES.md R1/R7). The
        rest, if the callee split further, are appended as-is. If the
        callee resolved to nothing at all (a trivially empty method),
        there's nothing to splice -- just continue past it.

        real_predecessor can be an entry node with no phase of its own --
        a fallback edge (PHASING_RULES.md R2 case 2) is sourced at the
        callee's own entry, not a real content node, when that callee's
        entire body dead-ends (e.g. Account.withdraw's two throw guards
        with no live branch, DESIGN.md §4.1/§8.6). The cascade still runs
        (its verdict is a meaningful `opened_by` reason), but there is
        nothing real to merge into, so it always opens fresh in that case.

        Returns the phase id call_site_id's own content now lives in (its
        first spliced phase), or None if it resolved to nothing -- needed
        so a dead-end SIBLING reached via the same fork (R4) can still
        find the right phase to merge into, even though call_site_id
        itself is never a member of a phase it did NOT resolve to alone
        (see visit()'s own use of this return value, and visit_children's
        anchor lookup).

        R6 refinement: when the callee resolves to EXACTLY one phase,
        call_site_id is unambiguous -- there's only one candidate for it
        to belong to -- so it's prepended into that phase as a real
        member, not just used as a comparison subject. This is the one
        case excluding it entirely would only lose a label for free; the
        ambiguity R6 exists to avoid is specific to the >1-phase case
        below, where a shared name really would leave it unclear which
        resulting phase it belongs to.
        """
        if len(callee_phases) == 1:
            callee_phases[0].nodes.insert(0, call_site_id)

        result_phase_id = None
        if callee_phases:
            first, *rest = callee_phases
            if real_predecessor is None:
                transition, merges, anchor = None, False, None
            else:
                gate = _is_gate(
                    real_predecessor, call_site_id, sequence_graph, back_edges
                )
                transition, merges = _evaluate(
                    subject, call_site_id, sequence_graph, data_graph, gate
                )
                anchor = phase_of.get(real_predecessor)
            if merges and anchor is not None:
                phases[anchor].nodes.extend(first.nodes)
                phases[anchor].transitions.append(transition)
                phases[anchor].transitions.extend(first.transitions)
                phase_of.update({n: anchor for n in first.nodes})
                result_phase_id = anchor
            else:
                first.opened_by = transition
                phases.append(first)
                phase_of.update({n: len(phases) - 1 for n in first.nodes})
                result_phase_id = len(phases) - 1
            for phase in rest:
                phases.append(phase)
                phase_of.update({n: len(phases) - 1 for n in phase.nodes})

        for real_tail, target_id in return_edges_by_call_site.get(call_site_id, []):
            visit(target_id, real_tail, call_site_id)

        return result_phase_id

    def visit_children(
        node_id: str, real_predecessor: str | None, subject: str | None
    ) -> None:
        successors = [
            s
            for s in sequence_graph.successors(node_id)
            if sequence_graph.edges[node_id, s].get("returnFrom") is None
        ]
        if real_predecessor is not None:
            for s in successors:
                visit(s, real_predecessor, subject)
            return

        # real_predecessor is None: node_id is an elided entry with no
        # phase of its own (R2). A live child just opens its own phase as
        # usual. If EVERY child here is a dead end (R4's degenerate case --
        # e.g. Account.withdraw's two throw guards with no visible normal-
        # completion branch, DESIGN.md §4.1), the first one visited opens
        # the phase the rest merge into, since there's no real predecessor
        # to anchor them to.
        live = [s for s in successors if not sequence_graph.nodes[s].get("deadEnd")]
        for s in live:
            visit(s, None, None)
        anchor = next((phase_of[s] for s in live if s in phase_of), None)
        for s in successors:
            if s in live or s in visited:
                continue
            visited.add(s)
            if anchor is None:
                anchor = open_phase(s, None)
            else:
                append(s, anchor, Transition(None, "dead-end", 0))
            _splice_own_invokes(s, anchor)

    def _splice_own_invokes(node_id: str, phase_id: int) -> None:
        """A dead end can still construct something before it terminates
        (e.g. the exception object itself, DESIGN.md §8.5) -- shown, but
        with nothing continuing past it, so its own callee's phases (if
        any) fold directly into this same phase rather than getting their
        own boundary."""
        for target_id in invoke_graph.successors(node_id):
            if invoke_graph.nodes[target_id]["type"] != "entry":
                continue
            for phase in _resolve_phases(
                target_id,
                sequence_graph,
                invoke_graph,
                data_graph,
                back_edges,
                return_edges_by_call_site,
            ):
                phases[phase_id].nodes.extend(phase.nodes)
                if phase.opened_by is not None:
                    phases[phase_id].transitions.append(phase.opened_by)
                phases[phase_id].transitions.extend(phase.transitions)
                phase_of.update({n: phase_id for n in phase.nodes})

    def visit(node_id: str, real_predecessor: str | None, subject: str | None) -> None:
        if node_id in visited:
            return
        visited.add(node_id)

        if sequence_graph.nodes[node_id]["type"] == "entry":
            # R2: never a phase member. Its own divergence is still real
            # gate evidence for its children (diverges(), used inside
            # visit_children via each child's own predecessor check); it
            # just has no subject of its own to attribute a transition to.
            visit_children(node_id, None, None)
            return

        if sequence_graph.nodes[node_id].get("deadEnd"):
            # R4: unconditional merge, never independently evaluated. Only
            # reached with a real predecessor -- a trace root is never a
            # dead end. real_predecessor can still be a phase-less entry
            # (see splice()'s docstring); with nothing real to merge into,
            # this just opens its own phase instead of crashing.
            anchor = phase_of.get(real_predecessor)
            transition = Transition(subject, "dead-end", 0)
            if anchor is not None:
                append(node_id, anchor, transition)
            else:
                anchor = open_phase(node_id, transition)
            _splice_own_invokes(node_id, anchor)
            return

        internal_targets = [
            t
            for t in invoke_graph.successors(node_id)
            if invoke_graph.nodes[t]["type"] == "entry"
        ]
        if internal_targets:
            # R6: node_id itself is never a member -- each resolved target
            # (more than one only for genuine polymorphism, DESIGN.md §3
            # bug fix #2) is spliced in on its own. node_id still gets a
            # phase_of entry pointing at its FIRST target's own resulting
            # phase (never a real member of it) -- a dead-end SIBLING
            # reached via the same fork (R4) needs this to find the right
            # phase to merge into via visit_children's anchor lookup, even
            # though node_id was excluded from that phase's own node list.
            spliced_id = None
            for target_id in internal_targets:
                callee_phases = _resolve_phases(
                    target_id,
                    sequence_graph,
                    invoke_graph,
                    data_graph,
                    back_edges,
                    return_edges_by_call_site,
                )
                this_target_id = splice(
                    real_predecessor, subject, node_id, callee_phases
                )
                if spliced_id is None:
                    spliced_id = this_target_id
            if spliced_id is not None:
                phase_of[node_id] = spliced_id
            return

        if real_predecessor is None:
            open_phase(node_id, None)
        else:
            gate = _is_gate(real_predecessor, node_id, sequence_graph, back_edges)
            transition, merges = _evaluate(
                subject, node_id, sequence_graph, data_graph, gate
            )
            anchor = phase_of.get(real_predecessor)
            if merges and anchor is not None:
                append(node_id, anchor, transition)
            else:
                open_phase(node_id, transition)

        visit_children(node_id, node_id, node_id)

    visit(entry_id, None, None)
    return phases


def build_phase_tree(graph: Graph) -> dict:
    """
    Segments a flattened interprocedural CFG into phases -- see
    PHASING_RULES.md for the rules and DESIGN.md §8 for how the input
    graph itself is built.
    """
    graphs = split_graph(graph)
    sequence_graph, invoke_graph, data_graph = (
        graphs["sequence"],
        graphs["invoke"],
        graphs["data"],
    )

    back_edges = compute_back_edges(
        _flow_graph(sequence_graph, invoke_graph), graph.rootId
    )
    return_edges_by_call_site = _return_edges_by_call_site(graph)

    phases = _resolve_phases(
        graph.rootId,
        sequence_graph,
        invoke_graph,
        data_graph,
        back_edges,
        return_edges_by_call_site,
    )
    return {
        "entryPoint": graph.entryPoint,
        "phases": [phase.to_dict() for phase in phases],
    }
