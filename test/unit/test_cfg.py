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
    filter_and_classify_roots_and_orphans,
    filter_noise_cfg,
    flatten_cfg,
    slice_from_root,
)
from backend.src.flowmap.model import Graph  # noqa: E402

def node(id_, type_, callee=None, caller=None, dead=False, terminus=None, arms=None, loops=None):
    """`arms` is the raw `branchArms` shape full_cfg.sc emits -- a list of
    (groupId, armLabel) pairs, since one call can be in several arms at
    once (an `if` inside a `try`)."""
    d = {"id": id_, "type": type_}
    if callee is not None:
        d["calleeFullName"] = callee
    if caller is not None:
        d["callerMethod"] = caller
    if dead:
        d["deadEnd"] = True
    if terminus is not None:
        d["terminus"] = terminus
    if arms:
        d["branchArms"] = [{"groupId": g, "armLabel": a} for g, a in arms]
    if loops:
        d["loopIds"] = list(loops)
    return d


def edge(frm, to, type_="sequence", loop_back=False):
    result = {"from": frm, "to": to, "type": type_}
    if loop_back:
        result["loopBack"] = True
    return result


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
            node("c1", "call", "doHelper", "doA", arms=[("cs_doA", "if")]),
            node("c2", "call", "doX", "doA"),
            node("m_doProcessTwo", "entry", "doProcessTwo"),
            node("c3", "call", "doHelper", "doProcessTwo"),
            node("c4", "call", "doY", "doProcessTwo"),
            node("m_doHelper", "entry", "doHelper"),
            node("c5", "call", "doInner", "doHelper", arms=[("cs_helper", "catch1")]),
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
        # Method-level metadata, reached by no edge -- one group in each
        # of the two independent chains, plus one in the method they
        # SHARE, which is what makes slice scoping observable.
        "branchGroups": [
            {
                "id": "cs_doA", "kind": "IF", "method": "doA", "line": 3,
                "arms": [{
                    "label": "if", "empty": False, "terminus": "continues",
                    "conditionCode": "x > 0", "firstCallId": "c1",
                }],
            },
            {
                "id": "cs_two", "kind": "IF", "method": "doProcessTwo", "line": 8,
                "arms": [{"label": "if", "empty": True, "terminus": "return"}],
            },
            {
                "id": "cs_helper", "kind": "TRY", "method": "doHelper", "line": 12,
                "arms": [
                    {"label": "catch1", "empty": False, "terminus": "throw",
                     "exceptionType": "java.lang.RuntimeException", "firstCallId": "c5"},
                    {"label": "noCatch", "empty": True, "terminus": "continues"},
                ],
            },
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

    def test_extraction_fields_survive_a_round_trip_unchanged(self):
        # Guards the whole extraction contract in one place: anything
        # full_cfg.sc emits and the model doesn't read is silently lost,
        # with no error anywhere to notice it by. This is exactly how the
        # node `branchArms` list, and both arm-level fields, went missing.
        original = _full_codebase_raw()
        round_tripped = Graph.from_dict(original).to_dict()
        self.assertEqual(round_tripped["branchGroups"], original["branchGroups"])
        self.assertEqual(round_tripped["nodes"], original["nodes"])
        self.assertEqual(round_tripped["edges"], original["edges"])


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

    def test_sequence_flow_counts_without_an_invoke_target(self):
        graph = Graph.from_dict({
            "nodes": [
                node("m_log", "entry", "log"),
                node("c_log", "call", "external.Logger.info", "log"),
            ],
            "edges": [edge("m_log", "c_log")],
        })

        classified = classify_roots_and_orphans(graph)

        self.assertEqual(classified.roots, ["m_log"])
        self.assertEqual(classified.orphans, [])

    def test_data_edges_do_not_make_an_entry_a_root(self):
        graph = Graph.from_dict({
            "nodes": [
                node("m_empty", "entry", "empty"),
                node("c_data", "call", "Thing.value", "empty"),
            ],
            "edges": [edge("m_empty", "c_data", "data")],
        })

        classified = classify_roots_and_orphans(graph)

        self.assertEqual(classified.roots, [])
        self.assertEqual(classified.orphans, ["m_empty"])

    def test_filter_then_reclassify_preserves_and_updates_every_entry(self):
        graph = Graph.from_dict({
            "nodes": [
                node("m_a", "entry", "A"),
                node("c_noise", "call", "<operator>.assignment", "A"),
                node("m_b", "entry", "B"),
                node("c_work", "call", "external.Work.run", "B"),
                node("m_empty", "entry", "Empty"),
            ],
            "edges": [
                edge("m_a", "c_noise"),
                edge("c_noise", "m_b", "invoke"),
                edge("m_b", "c_work"),
            ],
        })

        classified = filter_and_classify_roots_and_orphans(graph)

        self.assertEqual(classified.roots, ["m_b"])
        self.assertEqual(classified.orphans, ["m_a", "m_empty"])
        self.assertEqual(
            {n.id for n in classified.nodes if n.type == "entry"},
            {"m_a", "m_b", "m_empty"},
        )


# --------------------------------------------------------------------------
# Forward-only root slicing from the already-extracted full-codebase graph.
# --------------------------------------------------------------------------


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

    def test_branch_groups_are_carried_over_scoped_to_the_slice(self):
        # branchGroups is reached by no edge, so without explicit carry-over
        # it vanishes here and every downstream stage sees none. Scoped by
        # owning method: doA's slice gets its own group and the shared
        # callee's, never doProcessTwo's.
        sliced = slice_from_root(self.graph, "m_doA")
        self.assertEqual({g.id for g in sliced.branchGroups}, {"cs_doA", "cs_helper"})

        arm = next(g for g in sliced.branchGroups if g.id == "cs_doA").arms[0]
        self.assertEqual(arm.terminus, "continues")
        self.assertEqual(arm.conditionCode, "x > 0")

    def test_group_lacking_a_method_falls_back_to_tagged_nodes(self):
        # Graphs extracted before groups carried `method`. Scoping by node
        # tags still places cs_doA (c1 carries it); a group no node in the
        # slice claims is dropped rather than blindly carried.
        raw = _full_codebase_raw()
        for group in raw["branchGroups"]:
            del group["method"]
        sliced = slice_from_root(Graph.from_dict(raw), "m_doA")
        self.assertEqual({g.id for g in sliced.branchGroups}, {"cs_doA", "cs_helper"})


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

    def test_try_group_is_removed_when_every_catch_call_is_filtered(self):
        graph = Graph.from_dict({
            "entryPoint": "run",
            "nodes": [
                node("e", "entry", "run"),
                node("c_try", "call", "Service.work", "run"),
                node("c_noise", "call", "<operator>.assignment", "run",
                     arms=[("cs", "catch1")]),
                node("c_after", "call", "Service.after", "run"),
            ],
            "edges": [
                edge("e", "c_try"),
                edge("c_try", "c_noise"),
                edge("c_try", "c_after"),
                edge("c_noise", "c_after"),
            ],
            "branchGroups": [{
                "id": "cs", "kind": "TRY", "method": "run",
                "arms": [
                    {"label": "catch1", "empty": False, "terminus": "continues",
                     "exceptionType": "java.io.IOException", "firstCallId": "c_noise"},
                    {"label": "noCatch", "empty": True, "terminus": "continues"},
                ],
            }],
        })

        filtered = filter_noise_cfg(graph)

        self.assertEqual(filtered.branchGroups, [])
        self.assertIn(
            ("c_try", "c_after"),
            {(e.source, e.target) for e in filtered.edges},
        )

    def test_try_group_and_exception_type_survive_with_visible_catch_work(self):
        graph = Graph.from_dict({
            "entryPoint": "run",
            "nodes": [
                node("e", "entry", "run"),
                node("c_try", "call", "Service.work", "run"),
                node("c_catch", "call", "Report.reject", "run", arms=[("cs", "catch1")]),
                node("c_after", "call", "Service.after", "run"),
            ],
            "edges": [
                edge("e", "c_try"),
                edge("c_try", "c_catch"),
                edge("c_try", "c_after"),
                edge("c_catch", "c_after"),
            ],
            "branchGroups": [{
                "id": "cs", "kind": "TRY", "method": "run",
                "arms": [
                    {"label": "catch1", "empty": False, "terminus": "continues",
                     "exceptionType": "java.lang.IllegalArgumentException",
                     "firstCallId": "c_catch"},
                    {"label": "noCatch", "empty": True, "terminus": "continues"},
                ],
            }],
        })

        filtered = filter_noise_cfg(graph)

        self.assertEqual(len(filtered.branchGroups), 1)
        catch = next(a for a in filtered.branchGroups[0].arms if a.label == "catch1")
        self.assertEqual(catch.exceptionType, "java.lang.IllegalArgumentException")
        self.assertFalse(catch.empty)

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

    def test_a_stripped_arm_leaves_no_tag_on_whatever_follows_it(self):
        # if (x) { log(); }  -- the arm's ONLY call is stripped, so the
        # arm has nothing left. c_next is the convergence point AFTER the
        # branch, not part of it: bridging correctly reconnects the edge,
        # but the tag must NOT travel with it.
        #
        # This is why tag migration was removed. full_cfg.sc tags every
        # call in an arm, so a node genuinely inside one is always tagged
        # natively -- leaving "outside the arm" as the only place a
        # migrated tag could ever land. Confirmed live on the real graph:
        # a `return account.getBalance()` after a try block was picking up
        # that try's tag.
        graph = Graph.from_dict({
            "entryPoint": "run",
            "nodes": [
                node("e", "entry", "run"),
                node("c_log", "call", "java.io.PrintStream.println", "run", arms=[("cs1", "if")]),
                node("c_next", "call", "Service.fetch", "run"),
            ],
            "edges": [
                edge("e", "c_log"),
                edge("c_log", "c_next"),
            ],
        })
        filtered = filter_noise_cfg(graph)

        self.assertEqual({n.id for n in filtered.nodes}, {"e", "c_next"})
        c_next = next(n for n in filtered.nodes if n.id == "c_next")
        self.assertEqual(c_next.branchArms, [])
        # The edge itself is still bridged -- only the tag is withheld.
        self.assertIn(
            ("e", "c_next", "sequence"),
            {(e.source, e.target, e.type) for e in filtered.edges},
        )

    def test_a_surviving_arm_member_keeps_its_own_tags(self):
        # The other half: an arm whose first call is stripped but which
        # has a second, surviving call needs no migration at all -- that
        # call was tagged natively at extraction.
        graph = Graph.from_dict({
            "entryPoint": "run",
            "nodes": [
                node("e", "entry", "run"),
                node("op_assign", "call", "<operator>.assignment", "run", arms=[("cs1", "catch1")]),
                node("c_fetch", "call", "Service.fetch", "run",
                     arms=[("cs1", "catch1"), ("cs2", "if")]),
            ],
            "edges": [
                edge("e", "op_assign"),
                edge("op_assign", "c_fetch"),
            ],
        })
        filtered = filter_noise_cfg(graph)

        c_fetch = next(n for n in filtered.nodes if n.id == "c_fetch")
        self.assertEqual(
            {(t.groupId, t.armLabel) for t in c_fetch.branchArms},
            {("cs1", "catch1"), ("cs2", "if")},
        )


# --------------------------------------------------------------------------
# _recompute_branch_geometry: everything about a group that depends on
# which nodes survived -- arm empty/firstCallId, branchPointIds,
# convergesAt.
# --------------------------------------------------------------------------

def _if_else_raw(**arm_overrides) -> dict:
    """
    if (flag) { a(); }        <- cs1/if
    else      { b(); }        <- cs1/else
    join();                   <- convergence

    c_check is the condition call, left un-noisy so it survives to be a
    recognisable branch point.
    """
    raw = {
        "entryPoint": "run",
        "nodes": [
            node("e", "entry", "run"),
            node("c_check", "call", "Flags.check", "run"),
            node("c_a", "call", "Service.a", "run", arms=[("cs1", "if")]),
            node("c_b", "call", "Service.b", "run", arms=[("cs1", "else")]),
            node("c_join", "call", "Service.join", "run"),
        ],
        "edges": [
            edge("e", "c_check"),
            edge("c_check", "c_a"),
            edge("c_check", "c_b"),
            edge("c_a", "c_join"),
            edge("c_b", "c_join"),
        ],
        "branchGroups": [{
            "id": "cs1", "kind": "IF", "method": "run", "line": 3,
            "arms": [
                {"label": "if", "empty": False, "terminus": "continues",
                 "conditionCode": "flag", "firstCallId": "c_a"},
                {"label": "else", "empty": False, "terminus": "continues",
                 "firstCallId": "c_b"},
            ],
        }],
    }
    raw.update(arm_overrides)
    return raw


class RecomputeBranchGeometryTests(unittest.TestCase):
    def _group(self, raw: dict):
        return filter_noise_cfg(Graph.from_dict(raw)).branchGroups[0]

    def test_branch_point_of_a_plain_if_else(self):
        group = self._group(_if_else_raw())
        self.assertEqual(group.branchPointIds, ["c_check"])
        # convergesAt is a flatten-stage fact -- filter can't see past the
        # end of a method, so it is deliberately left unset here.
        self.assertIsNone(group.convergesAt)

    def test_first_call_id_is_cfg_order_not_ast_order(self):
        # `throw new Foo(bar())` -- extraction names the THROW as the arm's
        # first call (AST order), but the constructor runs first. The panel
        # must anchor on the constructor, and after filtering the throw
        # isn't even there.
        raw = _if_else_raw()
        raw["nodes"] = [
            node("e", "entry", "run"),
            node("c_check", "call", "Flags.check", "run"),
            node("c_ctor", "call", "Foo.<init>", "run", arms=[("cs1", "if")]),
            node("op_throw", "call", "<operator>.throw", "run",
                 arms=[("cs1", "if")], terminus="throw"),
            node("c_b", "call", "Service.b", "run", arms=[("cs1", "else")]),
            node("c_join", "call", "Service.join", "run"),
        ]
        raw["edges"] = [
            edge("e", "c_check"),
            edge("c_check", "c_ctor"),
            edge("c_ctor", "op_throw"),
            edge("c_check", "c_b"),
            edge("c_b", "c_join"),
        ]
        # AST-first, and about to be stripped as an <operator>.
        raw["branchGroups"][0]["arms"][0]["firstCallId"] = "op_throw"

        group = self._group(raw)
        if_arm = next(a for a in group.arms if a.label == "if")
        self.assertEqual(if_arm.firstCallId, "c_ctor")
        self.assertFalse(if_arm.empty)

    def test_arm_with_nothing_left_becomes_empty_with_no_first_call(self):
        raw = _if_else_raw()
        # else { y = x; } -- one assignment, stripped.
        raw["nodes"][3] = node(
            "c_b", "call", "<operator>.assignment", "run", arms=[("cs1", "else")]
        )
        group = self._group(raw)

        else_arm = next(a for a in group.arms if a.label == "else")
        self.assertTrue(else_arm.empty)
        self.assertIsNone(else_arm.firstCallId)
        # terminus is what still marks this as a skip rather than a stop --
        # the "skip to X" target itself waits for the flatten stage.
        self.assertEqual(else_arm.terminus, "continues")

    def test_branch_point_of_a_single_armed_guard(self):
        raw = _if_else_raw()
        raw["nodes"] = [
            node("e", "entry", "run"),
            node("c_check", "call", "Flags.check", "run"),
            node("c_ctor", "call", "Foo.<init>", "run",
                 arms=[("cs1", "if")], terminus="throw"),
            node("c_join", "call", "Service.join", "run"),
        ]
        raw["edges"] = [
            edge("e", "c_check"),
            edge("c_check", "c_ctor"),
            edge("c_check", "c_join"),
        ]
        raw["branchGroups"][0]["arms"] = [
            {"label": "if", "empty": False, "terminus": "throw",
             "conditionCode": "flag", "firstCallId": "c_ctor"},
        ]
        group = self._group(raw)

        self.assertEqual(group.branchPointIds, ["c_check"])

    def test_branch_point_inside_a_loop_is_the_condition_not_the_header(self):
        # while (...) { if (flag) a(); else b(); }  -- the arms hang off
        # c_check, not off the loop header they both cycle back to.
        raw = _if_else_raw()
        raw["edges"] = [
            edge("e", "c_head"),
            edge("c_head", "c_check"),
            edge("c_check", "c_a"),
            edge("c_check", "c_b"),
            edge("c_a", "c_head"),   # back edge
            edge("c_b", "c_head"),   # back edge
            edge("c_head", "c_join"),
        ]
        raw["nodes"].append(node("c_head", "call", "Iter.hasNext", "run"))
        group = self._group(raw)

        self.assertEqual(group.branchPointIds, ["c_check"])

    def test_a_try_forks_at_the_end_of_its_try_body(self):
        # Joern puts the exception edge at the END of the try body: the
        # try's tail has two successors, the handler and the normal
        # continuation. The method ENTRY is where the try starts, not
        # where anything splits, so it must not win.
        raw = {
            "entryPoint": "run",
            "nodes": [
                node("e", "entry", "run"),
                node("c_try", "call", "Service.a", "run"),
                node("c_catch", "call", "Err.getMessage", "run", arms=[("cs1", "catch1")]),
                node("c_after", "call", "Service.after", "run"),
            ],
            "edges": [
                edge("e", "c_try"),
                edge("c_try", "c_catch"),    # try tail -> handler
                edge("c_try", "c_after"),    # try tail -> normal continuation
                edge("c_catch", "c_after"),
            ],
            "branchGroups": [{
                "id": "cs1", "kind": "TRY", "method": "run", "line": 3,
                "arms": [
                    {"label": "catch1", "empty": False, "terminus": "continues",
                     "firstCallId": "c_catch"},
                    {"label": "noCatch", "empty": True, "terminus": "continues"},
                ],
            }],
        }
        group = self._group(raw)

        self.assertEqual(group.branchPointIds, ["c_try"])


# --------------------------------------------------------------------------
# flatten_cfg: inlines each internally-traversed callee at its own call
# site, cloning fresh every time -- one fixture per documented rule.
# --------------------------------------------------------------------------

# --------------------------------------------------------------------------
# Branch groups through flatten_cfg: instance-scoped ids, and convergesAt,
# which only becomes answerable once return edges exist.
# --------------------------------------------------------------------------

def _caller_convergence_raw() -> dict:
    """
    void a() { b(); c(); }
    void b() { if (x > 10) { e(); f(); } else { y = 10; } }

    The branch is the LAST thing in b(), so it converges on c() -- in the
    CALLER. Nothing connects the two before flattening, which is why this
    can't be a filter-stage fact. The else arm is empty (its assignment is
    stripped as noise), so it has no node and no edge of its own either.
    """
    return {
        "entryPoint": "a",
        "nodes": [
            node("e_a", "entry", "a"),
            node("c_b", "call", "b", "a"),
            node("c_c", "call", "pkg.C.run", "a"),
            node("e_b", "entry", "b"),
            node("c_e", "call", "pkg.E.run", "b", arms=[("cs1", "if")]),
            node("c_f", "call", "pkg.F.run", "b", arms=[("cs1", "if")]),
            node("leaf_c", "leaf", "pkg.C.run"),
            node("leaf_e", "leaf", "pkg.E.run"),
            node("leaf_f", "leaf", "pkg.F.run"),
        ],
        "edges": [
            edge("e_a", "c_b"),
            edge("c_b", "c_c"),
            edge("c_b", "e_b", "invoke"),
            edge("e_b", "c_e"),
            edge("c_e", "c_f"),
            edge("c_c", "leaf_c", "invoke"),
            edge("c_e", "leaf_e", "invoke"),
            edge("c_f", "leaf_f", "invoke"),
        ],
        "branchGroups": [{
            "id": "cs1", "kind": "IF", "method": "b", "line": 2,
            "branchPointIds": ["e_b"],
            "arms": [
                {"label": "if", "empty": False, "terminus": "continues",
                 "conditionCode": "x > 10", "firstCallId": "c_e"},
                {"label": "else", "empty": True, "terminus": "continues"},
            ],
        }],
    }


class FlattenBranchGroupTests(unittest.TestCase):
    def test_group_id_and_every_id_inside_it_are_instance_scoped(self):
        flattened = flatten_cfg(Graph.from_dict(_caller_convergence_raw()))
        group = flattened.branchGroups[0]

        self.assertTrue(group.id.startswith("cs1~"), group.id)
        # Every id the group holds points at a CLONE, not a pre-clone node.
        clone_ids = {n.id for n in flattened.nodes}
        self.assertEqual(len(group.branchPointIds), 1)
        self.assertIn(group.branchPointIds[0], clone_ids)
        self.assertIn(group.arms[0].firstCallId, clone_ids)
        # And the nodes' own tags were renamed to match.
        tags = {t.groupId for n in flattened.nodes for t in n.branchArms}
        self.assertEqual(tags, {group.id})

    def test_a_method_inlined_twice_yields_two_independent_groups(self):
        raw = _caller_convergence_raw()
        # a() { b(); c(); b(); } -- a second, independent call site.
        raw["nodes"].append(node("c_b2", "call", "b", "a"))
        raw["edges"].append(edge("c_c", "c_b2"))
        raw["edges"].append(edge("c_b2", "e_b", "invoke"))
        flattened = flatten_cfg(Graph.from_dict(raw))

        ids = [g.id for g in flattened.branchGroups]
        self.assertEqual(len(ids), 2, ids)
        self.assertEqual(len(set(ids)), 2, "the two instances must not share an id")
        # Each instance's nodes carry only their own group's id.
        for group in flattened.branchGroups:
            members = [n for n in flattened.nodes
                       if any(t.groupId == group.id for t in n.branchArms)]
            self.assertTrue(members)
            self.assertIn(group.arms[0].firstCallId, {n.id for n in members})

    def test_convergence_resolves_into_the_caller(self):
        flattened = flatten_cfg(Graph.from_dict(_caller_convergence_raw()))
        group = flattened.branchGroups[0]
        names = {n.id: n.calleeFullName for n in flattened.nodes}

        # c() -- reached by the if arm through b's return edge, and by the
        # empty else arm falling out of b entirely.
        self.assertIsNotNone(group.convergesAt)
        self.assertEqual(names[group.convergesAt], "pkg.C.run")

    def test_empty_tail_arm_gets_an_executable_return_route(self):
        flattened = flatten_cfg(Graph.from_dict(_caller_convergence_raw()))
        group = flattened.branchGroups[0]
        nodes = {node.id: node for node in flattened.nodes}

        routes = [
            edge for edge in flattened.edges
            if edge.type == "sequence"
            and edge.target == group.convergesAt
            and (group.id, "else") in {
                (requirement.groupId, requirement.armLabel)
                for requirement in edge.branchRequirements
            }
        ]

        self.assertEqual(len(routes), 1)
        route = routes[0]
        self.assertEqual(nodes[route.source].origId, "e_b")
        self.assertIsNotNone(route.returnFrom)
        self.assertEqual(nodes[route.returnFrom].origId, "c_b")

    def test_empty_tail_route_only_executes_for_its_arm(self):
        flattened = flatten_cfg(Graph.from_dict(_caller_convergence_raw()))
        group = flattened.branchGroups[0]
        route = next(
            edge for edge in flattened.edges
            if edge.type == "sequence"
            and edge.target == group.convergesAt
            and (group.id, "else") in {
                (requirement.groupId, requirement.armLabel)
                for requirement in edge.branchRequirements
            }
        )

        requirements = {
            (requirement.groupId, requirement.armLabel)
            for requirement in route.branchRequirements
        }
        self.assertIn((group.id, "else"), requirements)
        self.assertNotIn((group.id, "if"), requirements)

    def test_convergence_needs_every_arm_not_just_two(self):
        # if / else if / else where two arms continue past the branch and
        # the third returns. The statement AFTER the branch is reached by
        # two of the three, so a "two or more" rule picks it -- but the
        # returning arm never gets there. The real meeting point is where
        # the method itself returns to.
        raw = _caller_convergence_raw()
        raw["nodes"] += [
            node("c_g", "call", "pkg.G.run", "b", arms=[("cs1", "elseif1")]),
            node("c_h", "call", "pkg.H.run", "b", arms=[("cs1", "else")]),
            node("c_after_b", "call", "pkg.AFTER.run", "b"),
            node("leaf_g", "leaf", "pkg.G.run"),
            node("leaf_h", "leaf", "pkg.H.run"),
            node("leaf_after_b", "leaf", "pkg.AFTER.run"),
        ]
        raw["edges"] += [
            edge("e_b", "c_g"),
            edge("e_b", "c_h"),
            edge("c_f", "c_after_b"),      # if arm  -> after
            edge("c_g", "c_after_b"),      # elseif  -> after
            edge("c_g", "leaf_g", "invoke"),
            edge("c_h", "leaf_h", "invoke"),
            edge("c_after_b", "leaf_after_b", "invoke"),
        ]
        raw["branchGroups"][0]["arms"] = [
            {"label": "if", "empty": False, "terminus": "continues", "firstCallId": "c_e"},
            {"label": "elseif1", "empty": False, "terminus": "continues", "firstCallId": "c_g"},
            # returns: skips pkg.AFTER.run entirely
            {"label": "else", "empty": False, "terminus": "return", "firstCallId": "c_h"},
        ]
        flattened = flatten_cfg(Graph.from_dict(raw))
        group = flattened.branchGroups[0]
        names = {n.id: n.calleeFullName for n in flattened.nodes}

        self.assertEqual(names[group.convergesAt], "pkg.C.run")
        self.assertNotEqual(names[group.convergesAt], "pkg.AFTER.run")

    def test_returns_to_records_where_the_instance_continues(self):
        flattened = flatten_cfg(Graph.from_dict(_caller_convergence_raw()))
        group = flattened.branchGroups[0]
        names = {n.id: n.calleeFullName for n in flattened.nodes}

        # b()'s instance returns into c() -- the arrow target for any arm
        # of this group whose terminus is "return".
        self.assertEqual([names[i] for i in group.returnsTo], ["pkg.C.run"])

    def test_a_throwing_arm_contributes_no_path_so_nothing_converges(self):
        raw = _caller_convergence_raw()
        raw["nodes"][4] = node("c_e", "call", "pkg.E.run", "b",
                               arms=[("cs1", "if")], terminus="throw")
        raw["nodes"][5] = node("c_f", "call", "pkg.F.run", "b")
        raw["edges"] = [e for e in raw["edges"] if e != edge("c_e", "c_f")]
        raw["branchGroups"][0]["arms"] = [
            {"label": "if", "empty": False, "terminus": "throw", "firstCallId": "c_e"},
        ]
        flattened = flatten_cfg(Graph.from_dict(raw))

        self.assertIsNone(flattened.branchGroups[0].convergesAt)


# --------------------------------------------------------------------------
# Two SEQUENTIAL branches in one method, which after noise filtering both
# anchor on the method entry -- an IF's condition is `<operator>.*` and is
# stripped, so walking back from either branch's arm head lands on the same
# node. That is correct (the groups stay distinct by id and line, and render
# back-to-back), but it puts the FIRST branch's arms in the SECOND branch's
# view of "what the fork reaches without entering an arm", which is where
# both bugs below lived. Modelled on BankAccountService.transfer.
# --------------------------------------------------------------------------

def _sibling_branches_raw() -> dict:
    """
    void caller()   { transfer(); after(); }
    void transfer() {
        if (from == null) { throw new EXC(); }        <- guard, arm throws
        if (amount > P)      { premium(); }           <- fee chain, 3 arms
        else if (amount > S) { standard(); }
        else                 { fee = 0.0; }           <- empty, continues
        withdraw();                                   <- fee chain converges HERE
        deposit();
    }
    """
    return {
        "entryPoint": "caller",
        "nodes": [
            node("e_caller", "entry", "caller"),
            node("c_transfer", "call", "transfer", "caller"),
            node("c_after", "call", "pkg.AFTER.run", "caller"),
            node("e_transfer", "entry", "transfer"),
            node("c_guard", "call", "pkg.EXC.<init>", "transfer",
                 dead=True, terminus="throw", arms=[("cs_guard", "if")]),
            node("c_premium", "call", "pkg.FEE.premium", "transfer",
                 arms=[("cs_fee", "if")]),
            node("c_standard", "call", "pkg.FEE.standard", "transfer",
                 arms=[("cs_fee", "elseif1")]),
            node("c_withdraw", "call", "pkg.ACC.withdraw", "transfer"),
            node("c_deposit", "call", "pkg.ACC.deposit", "transfer"),
            node("leaf_after", "leaf", "pkg.AFTER.run"),
            node("leaf_exc", "leaf", "pkg.EXC.<init>"),
            node("leaf_premium", "leaf", "pkg.FEE.premium"),
            node("leaf_standard", "leaf", "pkg.FEE.standard"),
            node("leaf_withdraw", "leaf", "pkg.ACC.withdraw"),
            node("leaf_deposit", "leaf", "pkg.ACC.deposit"),
        ],
        "edges": [
            edge("e_caller", "c_transfer"),
            edge("c_transfer", "c_after"),
            edge("c_transfer", "e_transfer", "invoke"),
            # Both stripped conditions collapse onto the entry, so it forks
            # into the guard's throw, both fee arms, and the fall-through.
            edge("e_transfer", "c_guard"),
            edge("e_transfer", "c_premium"),
            edge("e_transfer", "c_standard"),
            edge("e_transfer", "c_withdraw"),
            edge("c_premium", "c_withdraw"),
            edge("c_standard", "c_withdraw"),
            edge("c_withdraw", "c_deposit"),
            edge("c_after", "leaf_after", "invoke"),
            edge("c_guard", "leaf_exc", "invoke"),
            edge("c_premium", "leaf_premium", "invoke"),
            edge("c_standard", "leaf_standard", "invoke"),
            edge("c_withdraw", "leaf_withdraw", "invoke"),
            edge("c_deposit", "leaf_deposit", "invoke"),
        ],
        "branchGroups": [
            {
                "id": "cs_guard", "kind": "IF", "method": "transfer", "line": 3,
                "branchPointIds": ["e_transfer"],
                "arms": [{
                    "label": "if", "empty": False, "terminus": "throw",
                    "conditionCode": "from == null", "firstCallId": "c_guard",
                }, {
                    "label": "else", "empty": True, "terminus": "continues",
                }],
            },
            {
                "id": "cs_fee", "kind": "IF", "method": "transfer", "line": 5,
                "branchPointIds": ["e_transfer"],
                "arms": [
                    {"label": "if", "empty": False, "terminus": "continues",
                     "conditionCode": "amount > P", "firstCallId": "c_premium"},
                    {"label": "elseif1", "empty": False, "terminus": "continues",
                     "conditionCode": "amount > S", "firstCallId": "c_standard"},
                    {"label": "else", "empty": True, "terminus": "continues"},
                ],
            },
        ],
    }


# --------------------------------------------------------------------------
# Convergence for the two shapes that used to report None: a guard clause
# (one live path) and a fork that is itself a call (its continuation exists
# only as a return edge). Both are the commonest shapes in real code, not
# corner cases -- 8 of 16 groups on the sample project were one or the other.
# --------------------------------------------------------------------------

def _guard_clause_raw() -> dict:
    """
    void caller()   { guarded(); after(); }
    void guarded()  { if (bad) { throw new EXC(); }  work(); }

    The `if` arm throws, so it contributes no path at all. The only live
    route out of the branch is the implicit `else` -- which full_cfg.sc now
    emits as a real (empty, continuing) arm. Convergence is where that
    surviving route resumes: work().
    """
    return {
        "entryPoint": "caller",
        "nodes": [
            node("e_caller", "entry", "caller"),
            node("c_guarded", "call", "guarded", "caller"),
            node("c_after", "call", "pkg.AFTER.run", "caller"),
            node("e_guarded", "entry", "guarded"),
            node("c_throw", "call", "pkg.EXC.<init>", "guarded",
                 dead=True, terminus="throw", arms=[("cs1", "if")]),
            node("c_work", "call", "pkg.WORK.run", "guarded"),
            node("leaf_after", "leaf", "pkg.AFTER.run"),
            node("leaf_exc", "leaf", "pkg.EXC.<init>"),
            node("leaf_work", "leaf", "pkg.WORK.run"),
        ],
        "edges": [
            edge("e_caller", "c_guarded"),
            edge("c_guarded", "c_after"),
            edge("c_guarded", "e_guarded", "invoke"),
            edge("e_guarded", "c_throw"),
            edge("e_guarded", "c_work"),
            edge("c_after", "leaf_after", "invoke"),
            edge("c_throw", "leaf_exc", "invoke"),
            edge("c_work", "leaf_work", "invoke"),
        ],
        "branchGroups": [{
            "id": "cs1", "kind": "IF", "method": "guarded", "line": 2,
            "branchPointIds": ["e_guarded"],
            "arms": [
                {"label": "if", "empty": False, "terminus": "throw",
                 "conditionCode": "bad", "firstCallId": "c_throw"},
                {"label": "else", "empty": True, "terminus": "continues"},
            ],
        }],
    }


def _call_fork_raw() -> dict:
    """
    void caller() { outer(); done(); }
    void outer()  { if (probe()) { armCall(); }  tail(); }
    void probe()  { deep(); }

    The fork is `probe()` -- a CALL with a body of its own. Flattening
    replaces a call site's own sequence edge with the callee's return edge,
    so post-flatten this branch point has NO sequence successors: both the
    arm and the implicit else are reached only through edges tagged
    returnFrom=probe.
    """
    return {
        "entryPoint": "caller",
        "nodes": [
            node("e_caller", "entry", "caller"),
            node("c_outer", "call", "outer", "caller"),
            node("c_done", "call", "pkg.DONE.run", "caller"),
            node("e_outer", "entry", "outer"),
            node("c_probe", "call", "probe", "outer"),
            node("c_arm", "call", "pkg.ARM.run", "outer", arms=[("cs1", "if")]),
            node("c_tail", "call", "pkg.TAIL.run", "outer"),
            node("e_probe", "entry", "probe"),
            node("c_deep", "call", "pkg.DEEP.run", "probe"),
            node("leaf_done", "leaf", "pkg.DONE.run"),
            node("leaf_arm", "leaf", "pkg.ARM.run"),
            node("leaf_tail", "leaf", "pkg.TAIL.run"),
            node("leaf_deep", "leaf", "pkg.DEEP.run"),
        ],
        "edges": [
            edge("e_caller", "c_outer"),
            edge("c_outer", "c_done"),
            edge("c_outer", "e_outer", "invoke"),
            edge("e_outer", "c_probe"),
            edge("c_probe", "e_probe", "invoke"),
            edge("c_probe", "c_arm"),
            edge("c_probe", "c_tail"),
            edge("c_arm", "c_tail"),
            edge("e_probe", "c_deep"),
            edge("c_done", "leaf_done", "invoke"),
            edge("c_arm", "leaf_arm", "invoke"),
            edge("c_tail", "leaf_tail", "invoke"),
            edge("c_deep", "leaf_deep", "invoke"),
        ],
        "branchGroups": [{
            "id": "cs1", "kind": "IF", "method": "outer", "line": 2,
            "branchPointIds": ["c_probe"],
            "arms": [
                {"label": "if", "empty": False, "terminus": "continues",
                 "conditionCode": "probe()", "firstCallId": "c_arm"},
                {"label": "else", "empty": True, "terminus": "continues"},
            ],
        }],
    }


def _tag_propagation_raw() -> dict:
    """
    void caller() { outer(); plain(); }
    void outer()  { if (x) { helper(); }  tail(); }
    void helper() { deep(); }

    helper() is the arm's only tagged call -- extraction tags LEXICALLY, so
    everything helper() goes on to do carries no tag at all, even though it
    runs solely because the arm was taken.

    caller() also calls helper's sibling plain() from OUTSIDE any arm, which
    is what makes flatten the only stage where this is answerable: pre-flatten
    there is one node per method body, shared by every caller.
    """
    return {
        "entryPoint": "caller",
        "nodes": [
            node("e_caller", "entry", "caller"),
            node("c_outer", "call", "outer", "caller"),
            node("c_plain", "call", "helper", "caller"),
            node("e_outer", "entry", "outer"),
            node("c_helper", "call", "helper", "outer", arms=[("cs1", "if")]),
            node("c_tail", "call", "pkg.TAIL.run", "outer"),
            node("e_helper", "entry", "helper"),
            node("c_deep", "call", "pkg.DEEP.run", "helper"),
            node("leaf_deep", "leaf", "pkg.DEEP.run"),
            node("leaf_tail", "leaf", "pkg.TAIL.run"),
        ],
        "edges": [
            edge("e_caller", "c_outer"),
            edge("c_outer", "c_plain"),
            edge("c_outer", "e_outer", "invoke"),
            edge("c_plain", "e_helper", "invoke"),
            edge("e_outer", "c_helper"),
            edge("e_outer", "c_tail"),
            edge("c_helper", "c_tail"),
            edge("c_helper", "e_helper", "invoke"),
            edge("e_helper", "c_deep"),
            edge("c_deep", "leaf_deep", "invoke"),
            edge("c_tail", "leaf_tail", "invoke"),
        ],
        "branchGroups": [{
            "id": "cs1", "kind": "IF", "method": "outer", "line": 2,
            "branchPointIds": ["e_outer"],
            "arms": [
                {"label": "if", "empty": False, "terminus": "continues",
                 "conditionCode": "x", "firstCallId": "c_helper"},
                {"label": "else", "empty": True, "terminus": "continues"},
            ],
        }],
    }


class ArmTagPropagationTests(unittest.TestCase):
    def _flattened(self):
        flattened = flatten_cfg(Graph.from_dict(_tag_propagation_raw()))
        group = flattened.branchGroups[0]
        in_arm = {
            n.origId for n in flattened.nodes
            if any(t.groupId == group.id and t.armLabel == "if" for t in n.branchArms)
        }
        return flattened, group, in_arm

    def test_everything_the_arm_causes_to_run_carries_its_tag(self):
        _, _, in_arm = self._flattened()

        # The tagged call itself, the callee it invokes, that callee's body,
        # and the external leaf underneath it.
        self.assertEqual(in_arm, {"c_helper", "e_helper", "c_deep", "leaf_deep"})

    def test_the_tag_stops_at_the_arm(self):
        _, _, in_arm = self._flattened()

        # tail() runs whether or not the branch was taken.
        self.assertNotIn("c_tail", in_arm)
        self.assertNotIn("e_outer", in_arm)
        self.assertNotIn("c_outer", in_arm)

    def test_the_same_callee_reached_outside_the_arm_stays_untagged(self):
        # The reason this belongs to flatten and cannot be done earlier:
        # helper() is ONE node before flattening, called both from inside
        # the arm and from caller() directly. Only the per-call-site clones
        # can carry different answers.
        flattened, group, _ = self._flattened()
        clones = [n for n in flattened.nodes if n.origId == "e_helper"]
        self.assertEqual(len(clones), 2, "helper should be inlined once per call site")

        tagged = [c for c in clones
                  if any(t.groupId == group.id for t in c.branchArms)]
        self.assertEqual(len(tagged), 1, "only the in-arm clone may carry the tag")

    def test_propagated_tags_are_instance_scoped(self):
        flattened, group, _ = self._flattened()
        # Not the pre-clone "cs1" -- the group id the flattened graph
        # actually holds, or nothing can match a group to its members.
        self.assertTrue(group.id.startswith("cs1~"), group.id)
        for n in flattened.nodes:
            for tag in n.branchArms:
                self.assertNotEqual(tag.groupId, "cs1", f"{n.id} kept a pre-clone id")


class GuardAndCallForkConvergenceTests(unittest.TestCase):
    def test_guard_clause_converges_where_the_surviving_path_resumes(self):
        flattened = flatten_cfg(Graph.from_dict(_guard_clause_raw()))
        group = flattened.branchGroups[0]
        names = {n.id: n.calleeFullName for n in flattened.nodes}

        # One live path is still a convergence: it is where the branch stops
        # mattering, which is the bound a branch panel needs. Requiring two
        # or more declined to answer for the commonest shape in real code.
        self.assertIsNotNone(group.convergesAt)
        self.assertEqual(names[group.convergesAt], "pkg.WORK.run")

    def test_a_fork_that_is_a_call_has_no_sequence_successors(self):
        # The premise of the next test, asserted so it cannot pass for the
        # wrong reason if flattening's edge convention ever changes.
        flattened = flatten_cfg(Graph.from_dict(_call_fork_raw()))
        fork = flattened.branchGroups[0].branchPointIds[0]

        plain = [e for e in flattened.edges
                 if e.source == fork and e.type == "sequence"]
        self.assertEqual(plain, [], "fork should own no sequence edge post-flatten")
        returns = [e.target for e in flattened.edges
                   if e.type == "sequence" and e.returnFrom == fork]
        self.assertTrue(returns, "its continuations must exist as return edges")

    def test_a_fork_that_is_a_call_still_finds_its_continuation(self):
        flattened = flatten_cfg(Graph.from_dict(_call_fork_raw()))
        group = flattened.branchGroups[0]
        names = {n.id: n.calleeFullName for n in flattened.nodes}

        self.assertIsNotNone(group.convergesAt)
        self.assertEqual(names[group.convergesAt], "pkg.TAIL.run")

    def test_a_guard_ending_its_method_converges_in_the_caller(self):
        # `void guarded() { if (bad) throw ...; }` -- nothing after the
        # guard at all. The surviving route is still real: not throwing
        # means the method returns and the CALLER carries on. Flattening
        # models that as a fallback edge off the callee's entry (its whole
        # body dead-ended, so nothing consumed the continuation), and
        # because that edge starts at the branch point it is picked up as
        # the implicit path.
        #
        # This was originally written asserting None, on the assumption
        # that removing work() removed the alternative. It does not -- the
        # fall-through survives the method, which is exactly what the
        # fallback edge is for.
        raw = _guard_clause_raw()
        drop = {"c_work", "leaf_work"}
        raw["nodes"] = [n for n in raw["nodes"] if n["id"] not in drop]
        raw["edges"] = [e for e in raw["edges"]
                        if e["from"] not in drop and e["to"] not in drop]
        raw["branchGroups"][0]["arms"] = [
            {"label": "if", "empty": False, "terminus": "throw", "firstCallId": "c_throw"},
            {"label": "else", "empty": True, "terminus": "continues"},
        ]
        flattened = flatten_cfg(Graph.from_dict(raw))
        group = flattened.branchGroups[0]
        names = {n.id: n.calleeFullName for n in flattened.nodes}

        self.assertEqual(names[group.convergesAt], "pkg.AFTER.run")


class SiblingBranchConvergenceTests(unittest.TestCase):
    def _fee_group(self, raw: dict):
        flattened = flatten_cfg(Graph.from_dict(raw))
        names = {n.id: n.calleeFullName for n in flattened.nodes}
        group = next(g for g in flattened.branchGroups if g.id.startswith("cs_fee~"))
        return group, names

    def test_the_two_groups_share_an_anchor_but_stay_distinguishable(self):
        # The premise of the tests below, asserted so a change to branch-point
        # derivation doesn't leave them passing for the wrong reason.
        flattened = flatten_cfg(Graph.from_dict(_sibling_branches_raw()))
        guard = next(g for g in flattened.branchGroups if g.id.startswith("cs_guard~"))
        fee = next(g for g in flattened.branchGroups if g.id.startswith("cs_fee~"))

        self.assertEqual(guard.branchPointIds, fee.branchPointIds)
        self.assertNotEqual(guard.id, fee.id)
        # ...and orderable, which is how the frontend renders them back-to-back.
        self.assertLess(guard.line, fee.line)

    def test_a_sibling_groups_throwing_arm_is_not_a_live_path(self):
        # The guard's `new EXC()` is a non-member successor of the shared
        # anchor, and is a proven dead end. Counting it as a path empties the
        # every-path intersection and reports None for a branch that plainly
        # converges.
        group, names = self._fee_group(_sibling_branches_raw())

        self.assertIsNotNone(group.convergesAt)
        self.assertEqual(names[group.convergesAt], "pkg.ACC.withdraw")

    def test_later_sibling_heads_require_the_earlier_guard_to_continue(self):
        flattened = flatten_cfg(Graph.from_dict(_sibling_branches_raw()))
        guard = next(g for g in flattened.branchGroups if g.id.startswith("cs_guard~"))
        fee = next(g for g in flattened.branchGroups if g.id.startswith("cs_fee~"))
        fee_heads = {arm.firstCallId for arm in fee.arms if arm.firstCallId is not None}
        routes = [edge for edge in flattened.edges if edge.target in fee_heads]
        guard_else = next(arm for arm in guard.arms if arm.label == "else")

        self.assertEqual(len(routes), 2)
        self.assertTrue(fee_heads.issubset(set(guard_else.targetIds or [])))
        for route in routes:
            requirements = {(r.groupId, r.armLabel) for r in route.branchRequirements}
            self.assertIn((guard.id, "else"), requirements)

    def test_empty_arm_does_not_fall_back_when_the_fork_continues_in_method(self):
        # The `else` arm is empty and continues, but the branch is NOT the
        # last thing in transfer() -- the skip has a real edge to withdraw().
        # Adding the enclosing frame's continuation as a further path drags
        # the answer up into the caller.
        group, names = self._fee_group(_sibling_branches_raw())

        self.assertEqual(names[group.convergesAt], "pkg.ACC.withdraw")
        self.assertNotEqual(names[group.convergesAt], "pkg.AFTER.run")

    def test_the_fallback_still_applies_when_the_branch_ends_the_method(self):
        # Same graph with everything after the fee chain removed: now the
        # empty arm really does fall out of transfer(), and the caller's
        # continuation IS the path it takes. Guards the fix against being
        # written as an unconditional removal.
        raw = _sibling_branches_raw()
        drop = {"c_withdraw", "c_deposit", "leaf_withdraw", "leaf_deposit"}
        raw["nodes"] = [n for n in raw["nodes"] if n["id"] not in drop]
        raw["edges"] = [
            e for e in raw["edges"]
            if e["from"] not in drop and e["to"] not in drop
        ]
        group, names = self._fee_group(raw)

        self.assertEqual(names[group.convergesAt], "pkg.AFTER.run")


def _nested_return_before_later_branch_raw() -> dict:
    """The reduced control-flow shape from OrderController.newOrder().

    The stripped conditions all project onto ``c_session``.  The first
    outer branch contains a nested if whose two arms return; only the outer
    false route can ever reach the later branch.  A selection for that later
    branch must therefore never become a requirement on either earlier
    returning route.
    """
    method = "OrderController.newOrder"
    return {
        "entryPoint": method,
        "nodes": [
            node("e", "entry", method),
            node("c_session", "call", "Session.getOrder", method),
            node("c_submit", "call", "OrderService.insertOrder", method,
                 arms=[("cs_confirmed", "if"), ("cs_present_confirm", "if")],
                 terminus="return"),
            node("c_error", "call", "Model.error", method,
                 arms=[("cs_confirmed", "if"), ("cs_present_confirm", "else")],
                 terminus="return"),
            node("c_update", "call", "Order.updateFields", method,
                 arms=[("cs_present_edit", "if")]),
            node("c_confirm", "call", "Session.confirm", method),
        ],
        "edges": [
            edge("e", "c_session"),
            edge("c_session", "c_submit"),
            edge("c_session", "c_error"),
            edge("c_session", "c_update"),
            edge("c_session", "c_confirm"),
            edge("c_update", "c_confirm"),
        ],
        "branchGroups": [
            {
                "id": "cs_confirmed", "kind": "IF", "method": method, "line": 10,
                "branchPointIds": ["c_session"],
                "arms": [
                    {"label": "if", "empty": False, "terminus": "return",
                     "conditionCode": "confirmed", "firstCallId": "c_submit"},
                    {"label": "else", "empty": True, "terminus": "continues"},
                ],
            },
            {
                "id": "cs_present_confirm", "kind": "IF", "method": method, "line": 11,
                "branchPointIds": ["c_session"],
                "arms": [
                    {"label": "if", "empty": False, "terminus": "return",
                     "conditionCode": "sessionOrder != null", "firstCallId": "c_submit"},
                    {"label": "else", "empty": False, "terminus": "return",
                     "firstCallId": "c_error"},
                ],
            },
            {
                "id": "cs_present_edit", "kind": "IF", "method": method, "line": 20,
                "branchPointIds": ["c_session"],
                "arms": [
                    {"label": "if", "empty": False, "terminus": "continues",
                     "conditionCode": "sessionOrder != null", "firstCallId": "c_update"},
                    {"label": "else", "empty": True, "terminus": "continues"},
                ],
            },
        ],
    }


class NestedReturningBranchRouteTests(unittest.TestCase):
    def test_nested_and_sequential_groups_keep_distinct_route_requirements(self):
        flattened = flatten_cfg(Graph.from_dict(_nested_return_before_later_branch_raw()))
        nodes_by_orig = {node.origId: node for node in flattened.nodes}
        groups = {group.id.split("~", 1)[0]: group for group in flattened.branchGroups}

        def route_to(orig_id: str):
            return next(
                edge for edge in flattened.edges
                if edge.source == nodes_by_orig["c_session"].id
                and edge.target == nodes_by_orig[orig_id].id
            )

        def requirements(orig_id: str) -> set[tuple[str, str]]:
            return {
                (requirement.groupId, requirement.armLabel)
                for requirement in route_to(orig_id).branchRequirements
            }

        submit_requirements = requirements("c_submit")
        self.assertEqual(
            submit_requirements,
            {
                (groups["cs_confirmed"].id, "if"),
                (groups["cs_present_confirm"].id, "if"),
            },
        )

        # This is the nested branch's other arm, still inside the outer IF.
        self.assertEqual(
            requirements("c_error"),
            {
                (groups["cs_confirmed"].id, "if"),
                (groups["cs_present_confirm"].id, "else"),
            },
        )

        # These are genuinely later routes, reached only through the outer
        # empty ELSE. The earlier nested group has already ceased to matter.
        self.assertEqual(
            requirements("c_update"),
            {
                (groups["cs_confirmed"].id, "else"),
                (groups["cs_present_edit"].id, "if"),
            },
        )
        self.assertEqual(
            requirements("c_confirm"),
            {
                (groups["cs_confirmed"].id, "else"),
                (groups["cs_present_edit"].id, "else"),
            },
        )

    def test_later_branch_never_controls_an_earlier_returning_route(self):
        flattened = flatten_cfg(Graph.from_dict(_nested_return_before_later_branch_raw()))
        nodes_by_orig = {node.origId: node for node in flattened.nodes}
        groups = {group.id.split("~", 1)[0]: group for group in flattened.branchGroups}
        into_submit = next(
            edge for edge in flattened.edges
            if edge.target == nodes_by_orig["c_submit"].id
        )
        requirements = {
            (requirement.groupId, requirement.armLabel)
            for requirement in into_submit.branchRequirements
        }

        self.assertIn((groups["cs_confirmed"].id, "if"), requirements)
        self.assertIn((groups["cs_present_confirm"].id, "if"), requirements)
        self.assertNotIn((groups["cs_present_edit"].id, "else"), requirements)


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

    def test_empty_normal_arm_owns_fallback_return_but_throw_arm_does_not(self):
        # transfer(): deposit(); after();
        # deposit(): if (amount <= 0) throw ...; balance += amount;
        #
        # The balance update is filtered noise, so flattening needs a
        # fallback entry->after edge for the empty normal arm. Selecting the
        # throwing arm must never make that normal return executable.
        graph = Graph.from_dict({
            "entryPoint": "transfer",
            "nodes": [
                node("e_transfer", "entry", "transfer"),
                node(
                    "c_deposit", "call", "Account.deposit", "transfer",
                ),
                node(
                    "c_catch", "call", "Ledger.noteAdjustment", "transfer",
                    dead=True, arms=[("outer_try", "catch1")],
                ),
                node("c_after", "call", "Account.getBalance", "transfer"),
                node("e_deposit", "entry", "Account.deposit"),
                node(
                    "c_throw", "call", "IllegalArgumentException.<init>",
                    "Account.deposit", dead=True, arms=[("deposit_guard", "if")],
                ),
                node("leaf_throw", "leaf", "IllegalArgumentException.<init>"),
                node("leaf_catch", "leaf", "Ledger.noteAdjustment"),
                node("leaf_after", "leaf", "Account.getBalance"),
            ],
            "edges": [
                edge("e_transfer", "c_deposit"),
                # A sibling route reaches the continuation before the
                # inlined deposit entry in global walk order. The empty arm
                # must still claim its direct fallback edge rather than a
                # later callee entry chosen as the convergence point.
                edge("e_transfer", "c_after"),
                edge("c_deposit", "c_catch"),
                edge("c_deposit", "c_after"),
                edge("c_deposit", "e_deposit", "invoke"),
                edge("e_deposit", "c_throw"),
                edge("c_throw", "leaf_throw", "invoke"),
                edge("c_catch", "leaf_catch", "invoke"),
                edge("c_after", "leaf_after", "invoke"),
            ],
            "branchGroups": [
                {
                    "id": "deposit_guard",
                    "kind": "IF",
                    "method": "Account.deposit",
                    "line": 30,
                    "branchPointIds": ["e_deposit"],
                    "arms": [
                        {
                            "label": "if",
                            "firstCallId": "c_throw",
                            "empty": False,
                            "terminus": "throw",
                            "conditionCode": "amount <= 0",
                        },
                        {"label": "else", "empty": True, "terminus": "continues"},
                    ],
                },
                {
                    "id": "outer_try",
                    "kind": "TRY",
                    "method": "transfer",
                    "line": 10,
                    "branchPointIds": ["c_deposit"],
                    "arms": [
                        {
                            "label": "catch1",
                            "firstCallId": "c_catch",
                            "empty": False,
                            "terminus": "throw",
                            "exceptionType": "java.lang.IllegalArgumentException",
                        },
                        {"label": "noCatch", "empty": True, "terminus": "continues"},
                    ],
                },
            ],
        })

        flattened = flatten_cfg(graph)
        group = next(g for g in flattened.branchGroups if g.id.startswith("deposit_guard~"))
        entry = next(
            n for n in flattened.nodes
            if n.type == "entry" and n.calleeFullName == "Account.deposit"
        )
        throw = next(n for n in flattened.nodes if n.origId == "c_throw")
        catch = next(n for n in flattened.nodes if n.origId == "c_catch")
        after = next(n for n in flattened.nodes if n.origId == "c_after")
        outer_try = next(g for g in flattened.branchGroups if g.id.startswith("outer_try~"))

        throw_edge = next(
            e for e in flattened.edges if e.source == entry.id and e.target == throw.id
        )
        normal_return = next(
            e for e in flattened.edges if e.source == entry.id and e.target == after.id
        )
        catch_route = next(
            e for e in flattened.edges if e.source == entry.id and e.target == catch.id
        )

        self.assertEqual(
            [(r.groupId, r.armLabel) for r in throw_edge.branchRequirements],
            [(group.id, "if")],
        )
        self.assertTrue(normal_return.fallback)
        self.assertEqual(
            {(r.groupId, r.armLabel) for r in normal_return.branchRequirements},
            {(group.id, "else"), (outer_try.id, "noCatch")},
        )
        # The catch is an exceptional continuation, not deposit()'s empty
        # normal arm. Selecting the nested throw plus catch reaches it;
        # selecting deposit's else does not control this edge.
        self.assertEqual(
            {(r.groupId, r.armLabel) for r in catch_route.branchRequirements},
            {(outer_try.id, "catch1")},
        )

    def test_loop_body_is_cloned_once_and_back_edge_is_metadata(self):
        # run(): while (...) { helper(); tail(); } after();
        # The body and helper subtree appear once. The repetition edge stays
        # in the CFG but is explicitly marked so a linear view can omit it.
        graph = Graph.from_dict({
            "entryPoint": "run",
            "nodes": [
                node("e_run", "entry", "run"),
                node("c_helper", "call", "helper", "run", loops=["loop1"]),
                node("c_tail", "call", "Worker.tail", "run", loops=["loop1"]),
                node("c_after", "call", "Worker.after", "run"),
                node("e_helper", "entry", "helper"),
                node("c_inner", "call", "Worker.inner", "helper"),
            ],
            "edges": [
                edge("e_run", "c_helper"),
                edge("c_helper", "c_tail"),
                edge("c_tail", "c_helper"),
                edge("c_tail", "c_after"),
                edge("c_helper", "e_helper", "invoke"),
                edge("e_helper", "c_inner"),
            ],
            "loopGroups": [{
                "id": "loop1", "kind": "WHILE", "method": "run",
                "line": 2, "conditionCode": "hasMore()",
            }],
        })

        flattened = flatten_cfg(graph)
        by_orig = {}
        for item in flattened.nodes:
            by_orig.setdefault(item.origId, []).append(item)

        for original_id in ("c_helper", "c_tail", "e_helper", "c_inner"):
            self.assertEqual(len(by_orig[original_id]), 1)
            self.assertEqual(by_orig[original_id][0].loopIds, ["loop1~0"])
        self.assertEqual(by_orig["c_after"][0].loopIds, [])

        self.assertEqual(
            [(loop.id, loop.kind, loop.conditionCode) for loop in flattened.loopGroups],
            [("loop1~0", "WHILE", "hasMore()")],
        )
        back_edges = [item for item in flattened.edges if item.loopBack]
        self.assertEqual(len(back_edges), 1)
        self.assertEqual(back_edges[0].source, by_orig["c_tail"][0].id)
        self.assertEqual(back_edges[0].target, by_orig["c_helper"][0].id)

    def test_loop_metadata_round_trips(self):
        raw = {
            "nodes": [node("e", "entry", "run", loops=["outer", "inner"])],
            "edges": [edge("a", "b", loop_back=True)],
            "loopGroups": [
                {"id": "outer", "kind": "FOR", "method": "run", "line": 1},
                {"id": "inner", "kind": "DO", "method": "run", "line": 2,
                 "conditionCode": "ready()"},
            ],
        }
        self.assertEqual(Graph.from_dict(raw).to_dict(), raw)

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

    def test_recursion_cutoff_stub_returns_to_the_pending_continuation(self):
        # run(): recurse(); after(); -- recurse(): recurse();
        # The revisited entry is the summary of all deeper frames. Its
        # return must reach after(), rather than a generic fallback being
        # attached to the first inlined recurse() entry.
        graph = Graph.from_dict({
            "entryPoint": "run",
            "nodes": [
                node("e_run", "entry", "run"),
                node("c_recurse", "call", "recurse", "run"),
                node("c_after", "call", "Worker.after", "run"),
                node("e_recurse", "entry", "recurse"),
                node("c_self", "call", "recurse", "recurse"),
                node("leaf_after", "leaf", "Worker.after"),
            ],
            "edges": [
                edge("e_run", "c_recurse"),
                edge("c_recurse", "c_after"),
                edge("c_recurse", "e_recurse", "invoke"),
                edge("e_recurse", "c_self"),
                edge("c_self", "e_recurse", "invoke"),
                edge("c_after", "leaf_after", "invoke"),
            ],
        })

        flattened = flatten_cfg(graph)
        recurse_entries = [
            n for n in flattened.nodes
            if n.type == "entry" and n.calleeFullName == "recurse"
        ]
        self.assertEqual(len(recurse_entries), 2)
        first, cutoff = sorted(recurse_entries, key=lambda n: n.depth)
        after = next(n for n in flattened.nodes if n.origId == "c_after")
        returns = [
            e for e in flattened.edges
            if e.type == "sequence" and e.target == after.id and e.returnFrom is not None
        ]

        self.assertEqual(len(returns), 1)
        self.assertEqual(returns[0].source, cutoff.id)
        self.assertNotEqual(returns[0].source, first.id)
        self.assertTrue(returns[0].fallback)

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


# --------------------------------------------------------------------------
# depth: the invoke-nesting level a CLONE was created at. The property that
# makes it a clone-tree fact and not an original-node one is that the same
# method reached at two nesting levels must come out in two columns.
# --------------------------------------------------------------------------

class FlattenDepthTests(unittest.TestCase):
    def _depths_of(self, flattened: Graph, callee: str, type_: str) -> list[int]:
        """A call SITE and the callee clone it invokes both carry the same
        calleeFullName but sit one column apart -- filter by node type or
        the two get mixed into one list."""
        return sorted(
            n.depth for n in flattened.nodes
            if n.calleeFullName == callee and n.type == type_
        )

    def _assert_every_invoke_deepens_by_one(self, flattened: Graph) -> None:
        """The whole point of the field: an "invoke" edge is a call, and a
        call always advances exactly one column. A delta of 0 renders a
        callee in its caller's own column -- the call looks like it didn't
        nest at all."""
        by_id = {n.id: n for n in flattened.nodes}
        for e in flattened.edges:
            if e.type != "invoke":
                continue
            source, target = by_id[e.source], by_id[e.target]
            self.assertEqual(
                target.depth, source.depth + 1,
                f"{source.calleeFullName} (d={source.depth}) -> "
                f"{target.calleeFullName} (d={target.depth})",
            )

    def test_same_method_inlined_at_two_levels_gets_two_depths(self):
        # run(): shallow(); deep();
        # deep():  mid();
        # mid():   shallow();
        # shallow() is invoked twice -- once directly from the root (depth
        # 1) and once from two levels down (depth 3). One depth per
        # ORIGINAL node cannot express that: a shortest-path answer keyed
        # by original id collapses both clones onto the shallower caller.
        graph = Graph.from_dict({
            "entryPoint": "run",
            "nodes": [
                node("e_run", "entry", "run"),
                node("c_shallow", "call", "shallow", "run"),
                node("c_deep", "call", "deep", "run"),
                node("e_deep", "entry", "deep"),
                node("c_mid", "call", "mid", "deep"),
                node("e_mid", "entry", "mid"),
                node("c_shallow2", "call", "shallow", "mid"),
                node("e_shallow", "entry", "shallow"),
                node("c_work", "call", "pkg.W.work", "shallow"),
                node("leaf_work", "leaf", "pkg.W.work"),
            ],
            "edges": [
                edge("e_run", "c_shallow"),
                edge("c_shallow", "c_deep"),
                edge("c_shallow", "e_shallow", "invoke"),
                edge("c_deep", "e_deep", "invoke"),
                edge("e_deep", "c_mid"),
                edge("c_mid", "e_mid", "invoke"),
                edge("e_mid", "c_shallow2"),
                edge("c_shallow2", "e_shallow", "invoke"),
                edge("e_shallow", "c_work"),
                edge("c_work", "leaf_work", "invoke"),
            ],
        })
        flattened = flatten_cfg(graph)

        # Two clones of shallow()'s entry, in different columns -- one per
        # call site, at that call site's own level + 1.
        self.assertEqual(self._depths_of(flattened, "shallow", "entry"), [1, 3])
        self.assertEqual(self._depths_of(flattened, "shallow", "call"), [0, 2])
        # ...and its whole body travels with it rather than staying put:
        # the work() call site inside each clone, and the leaf it invokes.
        self.assertEqual(self._depths_of(flattened, "pkg.W.work", "call"), [1, 3])
        self.assertEqual(self._depths_of(flattened, "pkg.W.work", "leaf"), [2, 4])
        self._assert_every_invoke_deepens_by_one(flattened)

    def test_leaf_callee_deepens_even_though_it_is_never_inlined(self):
        # An external callee has no entry node, so it never recurses
        # through inline() -- it is cloned inside its CALLER's own body.
        # It is still a call, and must still advance a column: validate()
        # and log() are the same leaf reached at depths 1 and 2.
        graph = Graph.from_dict({
            "entryPoint": "run",
            "nodes": [
                node("e_run", "entry", "run"),
                node("c_log_outer", "call", "pkg.Log.write", "run"),
                node("c_step", "call", "step", "run"),
                node("e_step", "entry", "step"),
                node("c_log_inner", "call", "pkg.Log.write", "step"),
                node("leaf_log", "leaf", "pkg.Log.write"),
            ],
            "edges": [
                edge("e_run", "c_log_outer"),
                edge("c_log_outer", "c_step"),
                edge("c_log_outer", "leaf_log", "invoke"),
                edge("c_step", "e_step", "invoke"),
                edge("e_step", "c_log_inner"),
                edge("c_log_inner", "leaf_log", "invoke"),
            ],
        })
        flattened = flatten_cfg(graph)

        leaf_depths = sorted(n.depth for n in flattened.nodes if n.type == "leaf")
        self.assertEqual(leaf_depths, [1, 2])
        self._assert_every_invoke_deepens_by_one(flattened)

    def test_root_is_zero_and_every_clone_is_stamped(self):
        graph = Graph.from_dict(_caller_convergence_raw())
        flattened = flatten_cfg(graph)

        by_id = {n.id: n for n in flattened.nodes}
        self.assertEqual(by_id[flattened.rootId].depth, 0)
        self.assertFalse([n.id for n in flattened.nodes if n.depth is None])
        self._assert_every_invoke_deepens_by_one(flattened)


if __name__ == "__main__":
    unittest.main()
