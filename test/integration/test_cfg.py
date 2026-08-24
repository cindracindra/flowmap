"""
Integration test: runs the REAL pipeline (joern-parse -> full_cfg.sc
over a live Joern server -> classify_roots_and_orphans) against
test_code/full_fixture, and checks the result matches the same
roots/orphans/shared-callee shape that test/test_full_cfg.py already
asserts against a hand-built dict -- see that file's
WholeCodebaseGraphShapeTests/ClassifyRootsAndOrphansTests for the
Python-only, no-Joern version of these same expectations.

Slow (JVM server boot + a real joern-parse on first run) and depends on
`joern` being installed and on PATH. NOTE: as of the test_cfg.py rename
(from full_cfg_pipeline_test.py), this is back in the DEFAULT fast test
loop -- `test/integration/__init__.py` already exists, and "test_cfg.py"
matches unittest discover's default "test*.py" pattern, so a plain
`poetry run python -m unittest discover -s test` now runs this too
(confirmed: 19 tests/~5ms -> 24 tests/~7s). If that's not intended,
either rename back to a *_test.py suffix (invisible to the default
"test*.py" prefix pattern) or move this out of test/ entirely. Run in
isolation with:

    poetry run python -m unittest discover -s test/integration -v
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

# `test/integration` itself, for the sibling `fixture` import below --
# and the repo root, for the `backend...` imports (both fixture.py's own
# internal ones and the two directly below) -- neither is on sys.path by
# default, and a dotted `test.integration.fixture`-style absolute import
# needs the repo root specifically, not this directory, which is why
# that form raised "No module named 'test.integration'": nothing had
# inserted the one directory that import actually needed.
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from fixture import (
    FULL_CFG_SC,
    CPG_PATH,
    OUTPUT_DIR,
    start_fixture_session,
)
from backend.src.flowmap.domain.cfg_pipeline import (
    classify_roots_and_orphans,
    slice_from_root,
    filter_noise_cfg,
    flatten_cfg
)
from backend.src.flowmap.model import Graph

def _simple_name(full_name: str) -> str:
    """'com.flowmap.fixture.OperationalChains.doA:void()' -> 'doA' --
    compares against the fixture's own method names instead of Joern's
    exact fullName/signature formatting, so the test isn't brittle to
    signature-string details unrelated to what it's actually checking."""
    return full_name.split(":", 1)[0].rsplit(".", 1)[-1]


class FullCfgPipelineTests(unittest.TestCase):
    """One shared session for the whole class -- the JVM server boot is
    the expensive part, so it's paid once in setUpClass, not per test."""

    @classmethod
    def setUpClass(cls):
        cls.session = start_fixture_session()
        # unittest does NOT call tearDownClass when setUpClass raises --
        # so ANYTHING below that can fail (including the debug dump)
        # needs to stay inside this try, or a failure there leaks the
        # server with nothing left to stop it. This bit twice already:
        # once for the original query/classify step, and again when the
        # debug dump was added after the try's closing brace instead of
        # inside it. `except BaseException` below (not `Exception`) is
        # for a third way this leaks: a Ctrl-C during this block raises
        # KeyboardInterrupt, which does NOT subclass Exception (by
        # design, so it isn't silently swallowed) -- an `except
        # Exception` guard lets an interrupt here skip cleanup entirely.
        try:
            raw = cls.session.query_script_json(FULL_CFG_SC)
            cls.graph = classify_roots_and_orphans(Graph.from_dict(raw))
            cls.by_id = {n.id: n for n in cls.graph.nodes}

            # FOR DEBUGGING: dump the raw full_cfg.sc output, and the
            # noise-filtered version, to files so you can inspect them in
            # a text editor or run Joern queries against them in the
            # Joern shell without having to re-run the whole pipeline.
            full_cfg_output_path = Path(OUTPUT_DIR) / "full_cfg.json"
            with full_cfg_output_path.open("w") as f:
                json.dump(raw, f, indent=2)

            filtered = filter_noise_cfg(cls.graph)
            filtered_cfg_output_path = Path(OUTPUT_DIR) / "filtered_cfg.json"
            with filtered_cfg_output_path.open("w") as f:
                json.dump(filtered.to_dict(), f, indent=2)

            # flatten_cfg needs a single-entry-point graph -- cls.graph
            # (and `filtered` above, its whole-graph filtered view) has
            # none, it's the whole multi-root codebase graph, not a
            # slice. Flatten each ROOT's own slice_from_root output
            # independently instead of the whole graph at once.
            flattened_by_root = [
                flatten_cfg(filter_noise_cfg(slice_from_root(cls.graph, root_id)))
                for root_id in cls.graph.roots
            ]
            flattened_cfg_output_path = Path(OUTPUT_DIR) / "flattened_cfg.json"
            with flattened_cfg_output_path.open("w") as f:
                json.dump([g.to_dict() for g in flattened_by_root], f, indent=2)

        except BaseException:
            cls.session.stop()
            raise

    @classmethod
    def tearDownClass(cls):
        cls.session.stop()

    def _entry_names(self, ids: list[str]) -> set[str]:
        return {_simple_name(self.by_id[i].calleeFullName) for i in ids}

    def test_cpg_file_was_created(self):
        self.assertTrue(CPG_PATH.exists(), "CPG file was not created.")

    def test_method_exit_nodes_are_retained(self):
        exits = [node for node in self.graph.nodes if node.type == "exit"]
        self.assertTrue(any(node.exitKind == "return" for node in exits))
        self.assertTrue(any(node.exitKind == "throw" for node in exits))
        self.assertTrue(any(node.exitKind == "fallthrough" for node in exits))

        exit_ids = {node.id for node in exits}
        self.assertTrue(any(
            edge.type == "sequence" and edge.target in exit_ids
            for edge in self.graph.edges
        ))

    def test_branch_arms_carry_path_level_exits_and_legacy_terminus(self):
        arms = [arm for group in self.graph.branchGroups for arm in group.arms]
        returning = next(arm for arm in arms if arm.terminus == "return")
        self.assertTrue(returning.exits)
        self.assertIn("return", {exit_.kind for exit_ in returning.exits})

    def test_roots_are_the_uncalled_methods(self):
        self.assertEqual(self._entry_names(self.graph.roots), {"doA", "doProcessTwo", "main"})

    def test_orphan_is_unused_method(self):
        self.assertEqual(self._entry_names(self.graph.orphans), {"unusedMethod", "<init>"})

    def test_shared_helper_is_neither_root_nor_orphan(self):
        helper_ids = {
            n.id for n in self.graph.nodes
            if n.type == "entry" and _simple_name(n.calleeFullName or "") == "doHelper"
        }
        self.assertTrue(helper_ids, "doHelper entry node not found in extracted graph")
        self.assertFalse(helper_ids & set(self.graph.roots))
        self.assertFalse(helper_ids & set(self.graph.orphans))

    def test_every_extracted_call_has_semantic_features(self):
        call_ids = {node.id for node in self.graph.nodes if node.type == "call"}
        self.assertEqual(set(self.graph.semanticFeatures), call_ids)

    def test_semantic_features_capture_receiver_arguments_and_method_terms(self):
        println = next(
            node for node in self.graph.nodes
            if node.type == "call"
            and node.calleeFullName == "java.io.PrintStream.println:void(java.lang.String)"
        )
        features = self.graph.semanticFeatures[println.id]

        self.assertEqual(features.receiver, "System.out")
        self.assertEqual(features.receiverType, "java.io.PrintStream")
        self.assertTrue(features.arguments)
        self.assertEqual(features.methodTerms, ["println"])
        self.assertIn("arguments", features.observedFeatures)

    def test_zero_call_early_return_branch_keeps_terminus_and_anchor(self):
        group = next(
            group for group in self.graph.branchGroups
            if _simple_name(group.method or "") == "earlyReturn"
        )

        self.assertEqual([arm.terminus for arm in group.arms], ["return", "continues"])
        self.assertTrue(all(arm.empty for arm in group.arms))
        self.assertEqual(len(group.branchPointIds), 1)
        anchor = self.by_id[group.branchPointIds[0]]
        self.assertEqual(_simple_name(anchor.calleeFullName or ""), "doInner")

    def test_zero_call_return_branch_anchors_on_its_condition_call(self):
        group = next(
            group for group in self.graph.branchGroups
            if _simple_name(group.method or "") == "callConditionReturn"
        )

        self.assertEqual([arm.terminus for arm in group.arms], ["return", "continues"])
        self.assertTrue(all(arm.empty for arm in group.arms))
        self.assertEqual(len(group.branchPointIds), 1)
        anchor = self.by_id[group.branchPointIds[0]]
        self.assertEqual(_simple_name(anchor.calleeFullName or ""), "hasRole")

    def test_short_circuit_zero_call_return_uses_last_visible_condition_call(self):
        group = next(
            group for group in self.graph.branchGroups
            if _simple_name(group.method or "") == "shortCircuitCallConditionReturn"
        )

        self.assertEqual([arm.terminus for arm in group.arms], ["return", "continues"])
        self.assertTrue(all(arm.empty for arm in group.arms))
        self.assertEqual(len(group.branchPointIds), 1)
        anchor = self.by_id[group.branchPointIds[0]]
        self.assertEqual(_simple_name(anchor.calleeFullName or ""), "isOwner")

    def test_doHelper_is_invoked_from_both_doA_and_doProcessTwo(self):
        helper_id = next(
            n.id for n in self.graph.nodes
            if n.type == "entry" and _simple_name(n.calleeFullName or "") == "doHelper"
        )
        callers = {
            _simple_name(self.by_id[e.source].callerMethod or "")
            for e in self.graph.edges
            if e.type == "invoke" and e.target == helper_id
        }
        self.assertEqual(callers, {"doA", "doProcessTwo"})

    def test_lambda_is_linked_to_its_enclosing_flow(self):
        lambda_entry = next(
            node for node in self.graph.nodes
            if node.type == "entry" and _simple_name(node.calleeFullName or "").startswith("<lambda>")
        )
        incoming = [
            edge for edge in self.graph.edges
            if edge.type == "invoke" and edge.target == lambda_entry.id
        ]

        self.assertTrue(incoming, "lambda entry has no incoming invoke edge")
        self.assertNotIn(lambda_entry.id, self.graph.roots)
        self.assertNotIn(lambda_entry.id, self.graph.orphans)
        self.assertEqual(
            {_simple_name(self.by_id[edge.source].callerMethod or "") for edge in incoming},
            {"doA"},
        )

        do_a_slice = slice_from_root(self.graph, self._entry_id("doA"))
        self.assertIn(lambda_entry.id, {node.id for node in do_a_slice.nodes})

    def _entry_id(self, simple_name: str) -> str:
        return next(
            n.id for n in self.graph.nodes
            if n.type == "entry" and _simple_name(n.calleeFullName or "") == simple_name
        )

if __name__ == "__main__":
    unittest.main()
