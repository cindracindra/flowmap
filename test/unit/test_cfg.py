from __future__ import annotations

import dataclasses
import sys
import unittest
from pathlib import Path

# Repo root, for the backend.src.flowmap... imports below -- test/unit/
# is two levels below it, not one (this used to point at a nonexistent
# test/processor and only worked by accident of `python -m unittest`
# prepending cwd to sys.path when run from the repo root).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from backend.src.flowmap.domain.cfg_pipeline import (  # noqa: E402
    classify_roots_and_orphans,
    filter_noise_cfg,
    find_roots_above,
    flatten_cfg,
    slice_anchored_cfg,
    slice_from_root,
)
from backend.src.flowmap.model import Graph  # noqa: E402

def node(id_, type_, callee=None, caller=None, dead=False, terminus=None, branch_group=None, arm_label=None):
    d = {"id": id_, "type": type_}
    if callee is not None:
        d["calleeFullName"] = callee
    if caller is not None:
        d["callerMethod"] = caller
    if dead:
        d["deadEnd"] = True
    if terminus is not None:
        d["terminus"] = terminus
    if branch_group is not None:
        d["branchGroupId"] = branch_group
    if arm_label is not None:
        d["armLabel"] = arm_label
    return d


def edge(frm, to, type_="sequence"):
    return {"from": frm, "to": to, "type": type_}


# --------------------------------------------------------------------------
# Fixture: doA() and doProcessTwo() are two unrelated call chains that both
# invoke the SAME doHelper() -- exactly the "method called from two
# operational processes" shape, showing how the raw dump represents it: one
# shared "entry" node for doHelper, reached by two separate "invoke" edges
# (one per call site), never cloned. unusedMethod() is a true orphan --
# nothing calls it, it calls nothing.
#
#   void doA()          { doHelper(); doX(); }
#   void doProcessTwo()  { doHelper(); doY(); }
#   void doHelper()      { doInner(); }
#   void unusedMethod()  { }
# --------------------------------------------------------------------------

def _full_codebase_raw() -> dict:
    return {
        "nodes": [
            node("m_doA", "entry", "doA"),
            node("c1", "call", "doHelper", "doA"),
            node("c2", "call", "doX", "doA"),
            node("m_doProcessTwo", "entry", "doProcessTwo"),
            node("c3", "call", "doHelper", "doProcessTwo"),
            node("c4", "call", "doY", "doProcessTwo"),
            node("m_doHelper", "entry", "doHelper"),
            node("c5", "call", "doInner", "doHelper"),
            node("m_unused", "entry", "unusedMethod"),
            node("leaf_doX", "leaf", "doX"),
            node("leaf_doY", "leaf", "doY"),
            node("leaf_doInner", "leaf", "doInner"),
        ],
        "edges": [
            edge("m_doA", "c1"),
            edge("c1", "c2"),
            edge("c1", "m_doHelper", "invoke"),
            edge("c2", "leaf_doX", "invoke"),
            edge("m_doProcessTwo", "c3"),
            edge("c3", "c4"),
            edge("c3", "m_doHelper", "invoke"),
            edge("c4", "leaf_doY", "invoke"),
            edge("m_doHelper", "c5"),
            edge("c5", "leaf_doInner", "invoke"),
        ],
    }


class WholeCodebaseGraphShapeTests(unittest.TestCase):
    """What extract_full_intermethod_cfg's Graph looks like before
    classification: no entryPoint, no roots/orphans yet."""

    def test_from_dict_has_no_entry_point(self):
        graph = Graph.from_dict(_full_codebase_raw())
        self.assertIsNone(graph.entryPoint)
        self.assertEqual(graph.roots, [])
        self.assertEqual(graph.orphans, [])
        self.assertEqual(len(graph.nodes), 12)
        self.assertEqual(len(graph.edges), 10)

    def test_to_dict_omits_entry_point_and_empty_roots_orphans(self):
        raw = Graph.from_dict(_full_codebase_raw()).to_dict()
        self.assertNotIn("entryPoint", raw)
        self.assertNotIn("roots", raw)
        self.assertNotIn("orphans", raw)
        self.assertIn("nodes", raw)
        self.assertIn("edges", raw)


class ClassifyRootsAndOrphansTests(unittest.TestCase):
    def setUp(self):
        self.graph = classify_roots_and_orphans(Graph.from_dict(_full_codebase_raw()))

    def test_both_unrelated_callers_are_roots(self):
        self.assertEqual(self.graph.roots, ["m_doA", "m_doProcessTwo"])

    def test_shared_callee_is_neither_root_nor_orphan(self):
        self.assertNotIn("m_doHelper", self.graph.roots)
        self.assertNotIn("m_doHelper", self.graph.orphans)

    def test_unreached_method_is_an_orphan(self):
        self.assertEqual(self.graph.orphans, ["m_unused"])

    def test_leaf_nodes_never_appear_in_either_list(self):
        leaf_ids = {n.id for n in self.graph.nodes if n.type == "leaf"}
        self.assertFalse(leaf_ids & set(self.graph.roots))
        self.assertFalse(leaf_ids & set(self.graph.orphans))

    def test_classification_does_not_mutate_the_input_graph(self):
        original = Graph.from_dict(_full_codebase_raw())
        classify_roots_and_orphans(original)
        self.assertEqual(original.roots, [])
        self.assertEqual(original.orphans, [])

    def test_to_dict_shape_after_classification(self):
        raw = self.graph.to_dict()
        self.assertNotIn("entryPoint", raw)
        self.assertEqual(raw["roots"], ["m_doA", "m_doProcessTwo"])
        self.assertEqual(raw["orphans"], ["m_unused"])


# --------------------------------------------------------------------------
# find_roots_above / slice_from_root / slice_anchored_cfg: the "anchored
# CFG" pair (upward discovery, then a normal forward-only slice) -- see
# DESIGN discussion: these deliberately do NOT do a live Joern query, they
# work entirely off an already-extracted full-codebase Graph.
# --------------------------------------------------------------------------

class FindRootsAboveTests(unittest.TestCase):
    def setUp(self):
        self.graph = Graph.from_dict(_full_codebase_raw())

    def test_anchor_with_two_independent_callers_finds_both_roots(self):
        # doHelper is called from both doA and doProcessTwo -- two
        # disjoint chains, not one "the" chain.
        self.assertEqual(find_roots_above(self.graph, "m_doHelper"), ["m_doA", "m_doProcessTwo"])

    def test_anchor_that_is_already_a_root_returns_itself(self):
        self.assertEqual(find_roots_above(self.graph, "m_doA"), ["m_doA"])

    def test_orphan_anchor_returns_itself(self):
        # m_unused has no caller at all -- walk_up finds nothing above it
        # immediately, same code path as a genuine root.
        self.assertEqual(find_roots_above(self.graph, "m_unused"), ["m_unused"])

    def test_rejects_non_entry_anchor(self):
        with self.assertRaises(ValueError):
            find_roots_above(self.graph, "c1")  # a "call" node, not "entry"

    def test_closed_cycle_with_no_external_caller_returns_itself(self):
        # A <-> B, mutual recursion, nothing outside ever calls either --
        # no root exists upstream of either, so the anchor falls back to
        # being its own root rather than the walk finding nothing at all.
        cyclic = Graph.from_dict({
            "nodes": [
                node("m_A", "entry", "A"),
                node("c_to_B", "call", "B", "A"),
                node("m_B", "entry", "B"),
                node("c_to_A", "call", "A", "B"),
            ],
            "edges": [
                edge("m_A", "c_to_B"),
                edge("c_to_B", "m_B", "invoke"),
                edge("m_B", "c_to_A"),
                edge("c_to_A", "m_A", "invoke"),
            ],
        })
        self.assertEqual(find_roots_above(cyclic, "m_A"), ["m_A"])


class SliceFromRootTests(unittest.TestCase):
    def setUp(self):
        self.graph = Graph.from_dict(_full_codebase_raw())

    def test_slice_from_doA_has_entry_point_and_only_reachable_nodes(self):
        sliced = slice_from_root(self.graph, "m_doA")
        self.assertEqual(sliced.entryPoint, "doA")
        self.assertEqual(
            {n.id for n in sliced.nodes},
            {"m_doA", "c1", "c2", "m_doHelper", "c5", "leaf_doInner", "leaf_doX"},
        )
        # doProcessTwo's own branch must not leak into doA's slice, even
        # though they share m_doHelper.
        self.assertNotIn("m_doProcessTwo", {n.id for n in sliced.nodes})
        self.assertNotIn("c4", {n.id for n in sliced.nodes})  # doProcessTwo's call to doY

    def test_sliced_shape_matches_a_single_entry_point_extraction(self):
        # This is the contract that matters: filter_intermethod_cfg /
        # flatten_intermethod_cfg / build_phase_tree only ever look for a
        # dict with "entryPoint"/"nodes"/"edges", one "entry" node whose
        # calleeFullName matches entryPoint, and no clones -- exactly
        # what a live single-entry-point Joern extraction produces.
        sliced = slice_from_root(self.graph, "m_doA")
        raw = sliced.to_dict()
        self.assertEqual(raw["entryPoint"], "doA")
        matching_entries = [
            n for n in raw["nodes"] if n["type"] == "entry" and n["calleeFullName"] == "doA"
        ]
        self.assertEqual(len(matching_entries), 1)

    def test_rejects_non_entry_root(self):
        with self.assertRaises(ValueError):
            slice_from_root(self.graph, "c1")


class SliceAnchoredCfgTests(unittest.TestCase):
    def setUp(self):
        self.graph = Graph.from_dict(_full_codebase_raw())

    def test_shared_anchor_yields_two_independent_phaser_ready_chains(self):
        chains = slice_anchored_cfg(self.graph, "doHelper")
        self.assertEqual({c.entryPoint for c in chains}, {"doA", "doProcessTwo"})
        for chain in chains:
            self.assertIn("m_doHelper", {n.id for n in chain.nodes})

        doa_chain = next(c for c in chains if c.entryPoint == "doA")
        self.assertNotIn("m_doProcessTwo", {n.id for n in doa_chain.nodes})

    def test_unambiguous_anchor_yields_one_chain(self):
        chains = slice_anchored_cfg(self.graph, "unusedMethod")
        self.assertEqual(len(chains), 1)
        self.assertEqual(chains[0].entryPoint, "unusedMethod")


# --------------------------------------------------------------------------
# filter_noise_cfg: excludes noise/JDK call sites, bridging around each
# gap, and tags throw-truncated survivors deadEnd=True.
# --------------------------------------------------------------------------

class FilterNoiseCfgTests(unittest.TestCase):
    def test_no_noise_means_no_dead_ends_and_unchanged_graph(self):
        graph = Graph.from_dict({
            "entryPoint": "run",
            "nodes": [node("e", "entry", "run"), node("c", "call", "Logger.log", "run")],
            "edges": [edge("e", "c")],
        })
        filtered = filter_noise_cfg(graph)
        self.assertEqual({n.id for n in filtered.nodes}, {"e", "c"})
        self.assertFalse(any(n.deadEnd for n in filtered.nodes))

    def test_noise_call_is_excluded_and_bridged_around(self):
        # run(): <operator>.assignment(); Logger.log(); -- A -> [noise] -> C
        # collapses to A -> C instead of leaving a dangling edge/orphaned node.
        graph = Graph.from_dict({
            "entryPoint": "run",
            "nodes": [
                node("e", "entry", "run"),
                node("c_noise", "call", "<operator>.assignment", "run"),
                node("c_real", "call", "Logger.log", "run"),
            ],
            "edges": [
                edge("e", "c_noise"),
                edge("c_noise", "c_real"),
            ],
        })
        filtered = filter_noise_cfg(graph)
        self.assertEqual({n.id for n in filtered.nodes}, {"e", "c_real"})
        self.assertEqual(
            {(e.source, e.target, e.type) for e in filtered.edges},
            {("e", "c_real", "sequence")},
        )

    def test_leaf_disconnected_by_an_excluded_call_is_pruned(self):
        # run(): <operator>.println(...) -- a noise call whose only
        # target is a leaf that nothing else points at. Excluding the
        # call drops its outgoing "invoke" edge entirely (not bridged --
        # the edge's SOURCE is excluded, so it's just gone), leaving the
        # leaf with zero edges. It should be pruned, not left dangling.
        graph = Graph.from_dict({
            "entryPoint": "run",
            "nodes": [
                node("e", "entry", "run"),
                node("c_noise", "call", "<operator>.assignment", "run"),
                node("leaf_only_reachable_via_noise", "leaf", "SomeJdkThing.method"),
            ],
            "edges": [
                edge("e", "c_noise"),
                edge("c_noise", "leaf_only_reachable_via_noise", "invoke"),
            ],
        })
        filtered = filter_noise_cfg(graph)
        self.assertEqual({n.id for n in filtered.nodes}, {"e"})
        self.assertEqual(filtered.edges, [])

    def test_root_with_zero_edges_after_filtering_is_still_kept(self):
        # A method whose only call is noise: after filtering, its entry
        # node legitimately has zero edges (a body with no real calls),
        # but it's the root -- entryPoint must still resolve to it.
        graph = Graph.from_dict({
            "entryPoint": "run",
            "nodes": [
                node("e", "entry", "run"),
                node("c_noise", "call", "<operator>.assignment", "run"),
            ],
            "edges": [edge("e", "c_noise")],
        })
        filtered = filter_noise_cfg(graph)
        self.assertEqual({n.id for n in filtered.nodes}, {"e"})
        self.assertEqual(filtered.entryPoint, "run")

    def test_root_in_a_whole_codebase_graph_is_not_pruned(self):
        # Same shape as above, but as it actually happens in practice:
        # filter_noise_cfg run on the WHOLE multi-root graph (entryPoint
        # is None, roots/orphans already populated by
        # classify_roots_and_orphans), not a single-root slice. Confirmed
        # live against test_code/full_fixture: main()'s only content is a
        # println that gets filtered as JDK noise -- main is a root, but
        # entryPoint is None here, so ONLY the graph.roots exemption (not
        # the entryPoint one) can save it from being pruned right along
        # with genuine disconnected dead weight.
        graph = Graph.from_dict({
            "nodes": [
                node("e_main", "entry", "main"),
                node("c_println", "call", "java.io.PrintStream.println", "main"),
            ],
            "edges": [edge("e_main", "c_println")],
        })
        graph = dataclasses.replace(graph, roots=["e_main"])
        filtered = filter_noise_cfg(graph)
        self.assertEqual({n.id for n in filtered.nodes}, {"e_main"})

    def test_throw_guard_tagged_dead_end_not_bridged_through(self):
        # if (amount >= 0) throw new IllegalArgumentException(...);
        # ledger.debit(amount);
        # Same fixture as test_phaser.py's DeadEndTaggingTests -- the
        # exact scenario a throw's special-casing exists for: Joern wires
        # <operator>.throw's own successor as if it fell through to
        # whatever lexically follows (op_throw -> c_debit below), but
        # that never actually happens. Without stripping that edge before
        # bridging, c_ctor would incorrectly gain a direct edge to
        # c_debit, as if constructing the exception led to debiting the
        # ledger instead of exiting the method via the throw.
        #
        # c_ctor's terminus="throw" is what a real extraction (full_cfg.sc's
        # classifyTerminus) would tag it with, since its own forward walk
        # finds nothing but <operator>.throw -- deadEnd is sourced from
        # this field directly now (see cfg_pipeline.py's _tag_dead_ends),
        # not reconstructed from the pre/post-bridging edge diff.
        graph = Graph.from_dict({
            "entryPoint": "withdraw",
            "nodes": [
                node("e", "entry", "withdraw"),
                node("op_guard", "call", "<operator>.greaterEqualsThan", "withdraw"),
                node("c_ctor", "call", "IllegalArgumentException.<init>", "withdraw", terminus="throw"),
                node("op_throw", "call", "<operator>.throw", "withdraw"),
                node("c_debit", "call", "Ledger.debit", "withdraw"),
            ],
            "edges": [
                edge("e", "op_guard"),
                edge("op_guard", "c_ctor"),
                edge("c_ctor", "op_throw"),
                edge("op_throw", "c_debit"),  # Joern's cfgNext quirk
                edge("op_guard", "c_debit"),
            ],
        })
        filtered = filter_noise_cfg(graph)

        dead_ids = {n.id for n in filtered.nodes if n.deadEnd}
        self.assertEqual(dead_ids, {"c_ctor"})

        # The actual bug this guards against: no bridged edge pretending
        # the throw fell through to debit.
        self.assertNotIn(
            ("c_ctor", "c_debit", "sequence"),
            {(e.source, e.target, e.type) for e in filtered.edges},
        )
        # The genuinely live branch (condition false) still reaches debit.
        self.assertIn(
            ("e", "c_debit", "sequence"),
            {(e.source, e.target, e.type) for e in filtered.edges},
        )

    def test_branch_tag_migrates_when_its_own_node_is_noise(self):
        # else { result = service.fetch(); }
        # Joern's first call in this arm's AST subtree is
        # <operator>.assignment (the real call is nested inside it as an
        # argument), so that's the node full_cfg.sc's emitBranchGroup
        # tags with branchGroupId/armLabel -- and also exactly the shape
        # filter_noise_cfg strips as noise. The tag should end up on
        # c_fetch (the surviving descendant), not disappear with
        # op_assign.
        graph = Graph.from_dict({
            "entryPoint": "run",
            "nodes": [
                node("e", "entry", "run"),
                node(
                    "op_assign", "call", "<operator>.assignment", "run",
                    branch_group="cs1", arm_label="else",
                ),
                node("c_fetch", "call", "Service.fetch", "run"),
            ],
            "edges": [
                edge("e", "op_assign"),
                edge("op_assign", "c_fetch"),
            ],
        })
        filtered = filter_noise_cfg(graph)

        self.assertEqual({n.id for n in filtered.nodes}, {"e", "c_fetch"})
        c_fetch = next(n for n in filtered.nodes if n.id == "c_fetch")
        self.assertEqual(c_fetch.branchGroupId, "cs1")
        self.assertEqual(c_fetch.armLabel, "else")

    def test_branch_tag_not_migrated_onto_a_node_with_its_own_tag(self):
        # op_a (excluded, tagged cs1/then) resolves to c_shared -- but
        # c_shared already carries its OWN genuine tag (cs1/finally, as
        # if it were itself a real arm's first call, e.g. a shared
        # convergence point). Migration must never overwrite a node's own
        # tag with one inherited from an excluded predecessor.
        graph = Graph.from_dict({
            "entryPoint": "run",
            "nodes": [
                node("e", "entry", "run"),
                node("op_a", "call", "<operator>.assignment", "run", branch_group="cs1", arm_label="then"),
                node(
                    "c_shared", "call", "Service.fetch", "run",
                    branch_group="cs1", arm_label="finally",
                ),
            ],
            "edges": [
                edge("e", "op_a"),
                edge("op_a", "c_shared"),
            ],
        })
        filtered = filter_noise_cfg(graph)

        c_shared = next(n for n in filtered.nodes if n.id == "c_shared")
        self.assertEqual(c_shared.branchGroupId, "cs1")
        self.assertEqual(c_shared.armLabel, "finally")


# --------------------------------------------------------------------------
# flatten_cfg: inlines each internally-traversed callee at its own call
# site, cloning fresh every time -- one fixture per documented rule.
# --------------------------------------------------------------------------

class FlattenCfgTests(unittest.TestCase):
    def _by_orig_name(self, flattened: Graph) -> dict:
        """id -> calleeFullName for every flattened node, keyed by the
        cloned id (not origId) -- lets tests assert on structure without
        hardcoding the "~N" suffixes clone() mints."""
        return {n.id: n.calleeFullName for n in flattened.nodes}

    def test_non_tail_call_gets_return_edge_tagged_with_call_site(self):
        # transfer(): balanceUpdate(); recordTransfer();
        # balanceUpdate(): withdraw(); deposit();
        # balanceUpdate() is NOT the last call in transfer() (recordTransfer
        # follows), so it's a non-tail call: deposit()'s own tail must
        # return into recordTransfer(), attributed to the balanceUpdate()
        # call site specifically -- not wherever transfer()'s own outer
        # continuation was (there isn't one, it's the root).
        graph = Graph.from_dict({
            "entryPoint": "transfer",
            "nodes": [
                node("e_transfer", "entry", "transfer"),
                node("c_balanceUpdate", "call", "balanceUpdate", "transfer"),
                node("c_recordTransfer", "call", "pkg.Ledger.record", "transfer"),
                node("e_balanceUpdate", "entry", "balanceUpdate"),
                node("c_withdraw", "call", "pkg.Account.withdraw", "balanceUpdate"),
                node("c_deposit", "call", "pkg.Account.deposit", "balanceUpdate"),
                node("leaf_withdraw", "leaf", "pkg.Account.withdraw"),
                node("leaf_deposit", "leaf", "pkg.Account.deposit"),
                node("leaf_recordTransfer", "leaf", "pkg.Ledger.record"),
            ],
            "edges": [
                edge("e_transfer", "c_balanceUpdate"),
                edge("c_balanceUpdate", "c_recordTransfer"),
                edge("e_balanceUpdate", "c_withdraw"),
                edge("c_withdraw", "c_deposit"),
                edge("c_balanceUpdate", "e_balanceUpdate", "invoke"),
                edge("c_withdraw", "leaf_withdraw", "invoke"),
                edge("c_deposit", "leaf_deposit", "invoke"),
                edge("c_recordTransfer", "leaf_recordTransfer", "invoke"),
            ],
        })
        flattened = flatten_cfg(graph)
        names = self._by_orig_name(flattened)

        return_edges = [e for e in flattened.edges if e.returnFrom is not None]
        self.assertEqual(len(return_edges), 1)
        ret = return_edges[0]
        self.assertEqual(names[ret.source], "pkg.Account.deposit")
        self.assertEqual(names[ret.target], "pkg.Ledger.record")
        # Attributed to the balanceUpdate() call site, not transfer()'s
        # own (nonexistent) outer continuation.
        self.assertEqual(names[ret.returnFrom], "balanceUpdate")
        self.assertFalse(ret.fallback)

    def test_tail_call_propagates_continuation_unchanged(self):
        # run(): helper();  -- the ONLY call in run(), so it's a tail call.
        # helper(): inner();
        # Nothing "returns" anywhere: run() is the root, so its own
        # continuation is empty -- helper()'s tail correctly finds nothing
        # to return to, and (since it's a tail call, not a non-tail one)
        # that must NOT trigger a fallback edge either.
        graph = Graph.from_dict({
            "entryPoint": "run",
            "nodes": [
                node("e_run", "entry", "run"),
                node("c_helper", "call", "helper", "run"),
                node("e_helper", "entry", "helper"),
                node("c_inner", "call", "Worker.inner", "helper"),
                node("leaf_inner", "leaf", "Worker.inner"),
            ],
            "edges": [
                edge("e_run", "c_helper"),
                edge("c_helper", "e_helper", "invoke"),
                edge("e_helper", "c_inner"),
                edge("c_inner", "leaf_inner", "invoke"),
            ],
        })
        flattened = flatten_cfg(graph)
        self.assertFalse(any(e.returnFrom is not None for e in flattened.edges))
        self.assertFalse(any(e.fallback for e in flattened.edges))

    def test_dead_end_call_with_internal_target_still_inlines_but_never_returns(self):
        # run(): if (bad) doThrow();  -- c_call is tagged deadEnd (as
        # filter_noise_cfg would leave it), and its invoke target is a
        # real project method (internal entry), not a leaf -- the
        # InsufficientFundsException-shaped case: doThrow()'s own body
        # still gets inlined (it really executes), but nothing inside it
        # may propagate back into run(), and -- unlike the fallback case
        # below -- this must NOT synthesize a fallback edge either: a
        # proven throw is a genuine terminus, not an inferred one.
        graph = Graph.from_dict({
            "entryPoint": "run",
            "nodes": [
                node("e_run", "entry", "run"),
                node("c_call", "call", "doThrow", "run", dead=True),
                node("e_doThrow", "entry", "doThrow"),
                node("c_log", "call", "Logger.log", "doThrow"),
                node("leaf_log", "leaf", "Logger.log"),
            ],
            "edges": [
                edge("e_run", "c_call"),
                edge("c_call", "e_doThrow", "invoke"),
                edge("e_doThrow", "c_log"),
                edge("c_log", "leaf_log", "invoke"),
            ],
        })
        flattened = flatten_cfg(graph)
        names = self._by_orig_name(flattened)

        # doThrow() and its own Logger.log() call still show up.
        self.assertIn("doThrow", names.values())
        self.assertIn("Logger.log", names.values())
        # But nothing returns, and nothing falls back.
        self.assertFalse(any(e.returnFrom is not None for e in flattened.edges))
        self.assertFalse(any(e.fallback for e in flattened.edges))

    def test_fallback_edge_when_callee_subtree_never_reaches_continuation(self):
        # transfer(): withdraw(); deposit();
        # withdraw()'s ENTIRE body is two throw guards -- no visible
        # normal-completion branch at all (zero calls, invisible to this
        # call-projected CFG, same as a real Account.withdraw shaped this
        # way). deposit() must still be reachable via a synthesized
        # fallback edge, since nothing inside withdraw() ever "returns".
        graph = Graph.from_dict({
            "entryPoint": "transfer",
            "nodes": [
                node("e_transfer", "entry", "transfer"),
                node("c_withdraw", "call", "Account.withdraw", "transfer"),
                node("c_deposit", "call", "Account.deposit", "transfer"),
                node("e_withdraw", "entry", "Account.withdraw"),
                node("c_guard1", "call", "IllegalArgumentException.<init>", "Account.withdraw", dead=True),
                node("c_guard2", "call", "InsufficientFundsException.<init>", "Account.withdraw", dead=True),
                node("leaf_guard1", "leaf", "IllegalArgumentException.<init>"),
                node("leaf_guard2", "leaf", "InsufficientFundsException.<init>"),
                node("leaf_deposit", "leaf", "Account.deposit"),
            ],
            "edges": [
                edge("e_transfer", "c_withdraw"),
                edge("c_withdraw", "c_deposit"),
                edge("c_withdraw", "e_withdraw", "invoke"),
                edge("e_withdraw", "c_guard1"),
                edge("e_withdraw", "c_guard2"),
                edge("c_guard1", "leaf_guard1", "invoke"),
                edge("c_guard2", "leaf_guard2", "invoke"),
                edge("c_deposit", "leaf_deposit", "invoke"),
            ],
        })
        flattened = flatten_cfg(graph)
        names = self._by_orig_name(flattened)

        fallback_edges = [e for e in flattened.edges if e.fallback]
        self.assertEqual(len(fallback_edges), 1)
        fb = fallback_edges[0]
        self.assertEqual(names[fb.source], "Account.withdraw")
        self.assertEqual(names[fb.target], "Account.deposit")
        # Attributed to the withdraw() call site (R7-style convention),
        # not to withdraw()'s own entry (the fallback's real source).
        self.assertEqual(names[fb.returnFrom], "Account.withdraw")
        source_node = next(n for n in flattened.nodes if n.id == fb.source)
        self.assertEqual(source_node.type, "entry")

    def test_mutual_recursion_terminates_with_stub(self):
        # A: logStart(); B(); -- B: A();  (mutual, tail-call recursion).
        # Must not hang; the revisited method (A, from inside B) is cut
        # off as a bare, unwalked stub -- confirmed by A appearing only
        # ONCE as a real (non-stub) body: the stub clone gets an entry
        # node but no outgoing edges of its own.
        graph = Graph.from_dict({
            "entryPoint": "A",
            "nodes": [
                node("e_A", "entry", "A"),
                node("c_log", "call", "Logger.logStart", "A"),
                node("c_B", "call", "B", "A"),
                node("e_B", "entry", "B"),
                node("c_A_rec", "call", "A", "B"),
                node("leaf_log", "leaf", "Logger.logStart"),
            ],
            "edges": [
                edge("e_A", "c_log"),
                edge("c_log", "c_B"),
                edge("c_log", "leaf_log", "invoke"),
                edge("c_B", "e_B", "invoke"),
                edge("e_B", "c_A_rec"),
                edge("c_A_rec", "e_A", "invoke"),  # closes the cycle
            ],
        })
        flattened = flatten_cfg(graph)  # must not hang/raise
        names = self._by_orig_name(flattened)

        self.assertIn("Logger.logStart", names.values())
        # Two distinct clones of A's ENTRY exist: the real root, and the
        # recursion-cutoff stub -- the stub has no outgoing edges at all.
        # Filtered to type == "entry" specifically: the call site c_A_rec
        # (B's own call back into A) also has calleeFullName == "A" and
        # would otherwise be miscounted as a third "A".
        a_entry_ids = [n.id for n in flattened.nodes if n.type == "entry" and n.calleeFullName == "A"]
        self.assertEqual(len(a_entry_ids), 2)
        stub_id = next(i for i in a_entry_ids if i != flattened.rootId)
        self.assertFalse(any(e.source == stub_id for e in flattened.edges))

    def test_same_callee_cloned_independently_per_call_site(self):
        # run(): helper(); helper();  -- TWO distinct call sites invoking
        # the SAME original helper() entry. Each must get its own clone
        # of helper()'s whole body -- never shared, per DESIGN.md #8.2.
        graph = Graph.from_dict({
            "entryPoint": "run",
            "nodes": [
                node("e_run", "entry", "run"),
                node("c1", "call", "helper", "run"),
                node("c2", "call", "helper", "run"),
                node("e_helper", "entry", "helper"),
                node("c_inner", "call", "Worker.inner", "helper"),
                node("leaf_inner", "leaf", "Worker.inner"),
            ],
            "edges": [
                edge("e_run", "c1"),
                edge("c1", "c2"),
                edge("c1", "e_helper", "invoke"),
                edge("c2", "e_helper", "invoke"),  # same original target
                edge("e_helper", "c_inner"),
                edge("c_inner", "leaf_inner", "invoke"),
            ],
        })
        flattened = flatten_cfg(graph)

        # Filtered to type == "entry"/"call" respectively: the call
        # sites c1/c2 ALSO have calleeFullName == "helper" (they
        # represent "this call invokes helper"), and would otherwise be
        # miscounted alongside helper's own entry clones.
        helper_clone_ids = [
            n.id for n in flattened.nodes if n.type == "entry" and n.calleeFullName == "helper"
        ]
        inner_clone_ids = [
            n.id for n in flattened.nodes if n.type == "call" and n.calleeFullName == "Worker.inner"
        ]
        self.assertEqual(len(helper_clone_ids), 2)
        self.assertEqual(len(inner_clone_ids), 2)
        # And they're genuinely independent nodes, not aliases.
        self.assertEqual(len(set(helper_clone_ids)), 2)

    def test_polymorphic_call_site_splices_every_target(self):
        # run() calls shape.area() (Shape.area) which resolves to TWO real
        # implementations (Circle.area, Square.area), then logs.
        graph = Graph.from_dict({
            "entryPoint": "run",
            "nodes": [
                node("e_run", "entry", "run"),
                node("c_area", "call", "Shape.area", "run"),
                node("c_after", "call", "Logger.log", "run"),
                node("e_circle", "entry", "Circle.area"),
                node("c_circle_calc", "call", "Circle.calc", "Circle.area"),
                node("e_square", "entry", "Square.area"),
                node("c_square_calc", "call", "Square.calc", "Square.area"),
                node("leaf_circle_calc", "leaf", "Circle.calc"),
                node("leaf_square_calc", "leaf", "Square.calc"),
                node("leaf_after", "leaf", "Logger.log"),
            ],
            "edges": [
                edge("e_run", "c_area"),
                edge("c_area", "c_after"),
                edge("c_area", "e_circle", "invoke"),
                edge("c_area", "e_square", "invoke"),  # 2nd real target: polymorphism
                edge("e_circle", "c_circle_calc"),
                edge("c_circle_calc", "leaf_circle_calc", "invoke"),
                edge("e_square", "c_square_calc"),
                edge("c_square_calc", "leaf_square_calc", "invoke"),
                edge("c_after", "leaf_after", "invoke"),
            ],
        })
        flattened = flatten_cfg(graph)
        by_id = {n.id: n for n in flattened.nodes}

        calc_names = {n.calleeFullName for n in flattened.nodes if n.type == "call"}
        self.assertIn("Circle.calc", calc_names)
        self.assertIn("Square.calc", calc_names)

        # c_after (the CALL to Logger.log -- disambiguated from
        # leaf_after, which shares the same calleeFullName but is a
        # different node type) is reached from BOTH branches' own tails:
        # genuine convergence, in-degree 2, regardless of which branch
        # the walk happens to process first.
        after_clone_ids = [
            n.id for n in flattened.nodes
            if n.type == "call" and n.calleeFullName == "Logger.log"
        ]
        self.assertEqual(len(after_clone_ids), 1)  # c_after itself only clones once (run()'s own single path)
        incoming = [
            e for e in flattened.edges
            if e.target in after_clone_ids and e.type == "sequence"
        ]
        self.assertEqual(len(incoming), 2)
        sources = {by_id[e.source].calleeFullName for e in incoming}
        self.assertEqual(sources, {"Circle.calc", "Square.calc"})

    def test_data_edge_wired_only_within_same_cloned_instance(self):
        # run(): a = get(); use(a);  -- a "data" edge from get()'s call
        # site to use()'s, both within run()'s own body -- should survive
        # untouched (single instance, no cross-clone ambiguity).
        graph = Graph.from_dict({
            "entryPoint": "run",
            "nodes": [
                node("e_run", "entry", "run"),
                node("c_get", "call", "Store.get", "run"),
                node("c_use", "call", "Store.use", "run"),
                node("leaf_get", "leaf", "Store.get"),
                node("leaf_use", "leaf", "Store.use"),
            ],
            "edges": [
                edge("e_run", "c_get"),
                edge("c_get", "c_use"),
                edge("c_get", "leaf_get", "invoke"),
                edge("c_use", "leaf_use", "invoke"),
                edge("c_get", "c_use", "data"),
            ],
        })
        flattened = flatten_cfg(graph)
        names = self._by_orig_name(flattened)

        data_edges = [e for e in flattened.edges if e.type == "data"]
        self.assertEqual(len(data_edges), 1)
        self.assertEqual(names[data_edges[0].source], "Store.get")
        self.assertEqual(names[data_edges[0].target], "Store.use")


if __name__ == "__main__":
    unittest.main()
