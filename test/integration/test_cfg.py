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
from backend.src.flowmap.domain.method_branch_routing import prepare_method_branch_routes
from backend.src.flowmap.domain.method_scoping import build_method_definitions
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
            pre_filtered_output_path = Path(OUTPUT_DIR) / "pre_filtered_cfg.json"
            with pre_filtered_output_path.open("w") as f:
                json.dump(raw, f, indent=2)

            filtered = filter_noise_cfg(cls.graph)
            cls.filtered = filtered
            cls.filtered_by_id = {node.id: node for node in filtered.nodes}
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

    def _method_nodes(self, method_name: str):
        return [
            node for node in self.graph.nodes
            if _simple_name(node.callerMethod or "") == method_name
        ]

    def _method_sequence_pairs(self, method_name: str):
        nodes = self._method_nodes(method_name)
        ids = {node.id for node in nodes}
        return [
            (self.by_id[edge.source], self.by_id[edge.target])
            for edge in self.graph.edges
            if edge.type == "sequence"
            and edge.source in ids
            and edge.target in ids
        ]

    def _branch_groups(self, method_name: str):
        return [
            group for group in self.graph.branchGroups
            if _simple_name(group.method or "") == method_name
        ]

    def _method_entry(self, method_name: str):
        return next(
            node for node in self.graph.nodes
            if node.type == "entry"
            and _simple_name(node.calleeFullName or "") == method_name
        )

    def _sequence_reachable_without_terminals(
        self,
        method_name: str,
        target_id: str,
    ) -> bool:
        entry = self._method_entry(method_name)
        method_ids = {entry.id, *(node.id for node in self._method_nodes(method_name))}
        outgoing: dict[str, list[str]] = {}
        for edge in self.graph.edges:
            if (
                edge.type == "sequence"
                and edge.source in method_ids
                and edge.target in method_ids
            ):
                outgoing.setdefault(edge.source, []).append(edge.target)
        reached: set[str] = set()
        pending = [entry.id]
        while pending:
            node_id = pending.pop()
            if node_id in reached:
                continue
            reached.add(node_id)
            if node_id == target_id:
                return True
            node = self.by_id[node_id]
            if node.exitKind in {"return", "throw"}:
                continue
            if node.type == "call" and node.calleeFullName == "<operator>.throw":
                continue
            pending.extend(outgoing.get(node_id, ()))
        return False

    def _arm_nodes(self, group_id: str, arm_label: str):
        return [
            node for node in self.graph.nodes
            if any(
                ref.groupId == group_id and ref.armLabel == arm_label
                for ref in node.branchArms
            )
        ]

    def _arm_call_names(self, group_id: str, arm_label: str):
        return {
            _simple_name(node.calleeFullName or "")
            for node in self._arm_nodes(group_id, arm_label)
            if node.type == "call"
        }

    def test_if_arms_with_calls_keep_independent_members_and_local_convergence(self):
        group, = self._branch_groups("branchWithCalls")
        self.assertEqual(
            {arm.label: arm.terminus for arm in group.arms},
            {"if": "continues", "else": "continues"},
        )
        self.assertEqual(self._arm_call_names(group.id, "if"), {"doX"})
        self.assertEqual(self._arm_call_names(group.id, "else"), {"doInner"})

        helper = next(
            node for node in self._method_nodes("branchWithCalls")
            if node.type == "call"
            and _simple_name(node.calleeFullName or "") == "doHelper"
        )
        arm_tails = {
            node.id
            for label in ("if", "else")
            for node in self._arm_nodes(group.id, label)
            if node.type == "call"
        }
        self.assertTrue(all(any(
            edge.type == "sequence"
            and edge.source == tail_id
            and edge.target == helper.id
            for edge in self.graph.edges
        ) for tail_id in arm_tails))

    def test_empty_if_arm_continues_to_the_local_body(self):
        group, = self._branch_groups("emptyContinuingArm")
        empty_arm = next(arm for arm in group.arms if arm.label == "else")
        self.assertTrue(empty_arm.empty)
        self.assertEqual(empty_arm.terminus, "continues")
        self.assertEqual(self._arm_call_names(group.id, "else"), set())
        method_calls = {
            _simple_name(node.calleeFullName or "")
            for node in self._method_nodes("emptyContinuingArm")
            if node.type == "call"
        }
        self.assertIn("doHelper", method_calls)

    def test_zero_operation_return_arm_ends_at_explicit_exit(self):
        group, = self._branch_groups("emptyReturnArm")
        returning = next(arm for arm in group.arms if arm.terminus == "return")
        members = self._arm_nodes(group.id, returning.label)
        self.assertFalse(any(node.type == "call" for node in members))
        exits = [node for node in members if node.type == "exit"]
        self.assertTrue(exits)
        self.assertEqual({node.exitKind for node in exits}, {"return"})

        helper_ids = {
            node.id for node in self._method_nodes("emptyReturnArm")
            if node.type == "call"
            and _simple_name(node.calleeFullName or "") == "doHelper"
        }
        self.assertFalse(any(
            edge.type == "sequence"
            and edge.source in {node.id for node in exits}
            and edge.target in helper_ids
            for edge in self.graph.edges
        ))

    def test_zero_operation_throw_arm_ends_at_dead_end(self):
        group, = self._branch_groups("emptyThrowArm")
        throwing = next(arm for arm in group.arms if arm.terminus == "throw")
        members = self._arm_nodes(group.id, throwing.label)
        self.assertFalse(any(
            node.type == "call"
            and not (node.calleeFullName or "").startswith("<operator>.")
            for node in members
        ))
        exits = [node for node in members if node.type == "exit"]
        self.assertTrue(exits)
        self.assertEqual({node.exitKind for node in exits}, {"throw"})

        helper_ids = {
            node.id for node in self._method_nodes("emptyThrowArm")
            if node.type == "call"
            and _simple_name(node.calleeFullName or "") == "doHelper"
        }
        self.assertFalse(any(
            edge.type == "sequence"
            and edge.source in {node.id for node in exits}
            and edge.target in helper_ids
            for edge in self.graph.edges
        ))

    def test_nested_empty_branch_retains_nested_instance_membership(self):
        groups = self._branch_groups("nestedEmptyBranch")
        self.assertEqual(len(groups), 2)
        returning_group = next(
            group for group in groups
            if any(arm.terminus == "return" for arm in group.arms)
        )
        returning = next(
            arm for arm in returning_group.arms if arm.terminus == "return"
        )
        exits = [
            node for node in self._arm_nodes(returning_group.id, returning.label)
            if node.type == "exit"
        ]
        self.assertTrue(exits)
        self.assertTrue(all(len(node.branchArms) == 2 for node in exits))

        helper = next(
            node for node in self._method_nodes("nestedEmptyBranch")
            if node.type == "call"
            and _simple_name(node.calleeFullName or "") == "doHelper"
        )
        self.assertFalse(helper.branchArms)

    def test_branch_at_method_end_has_local_exits_but_no_call_continuation(self):
        group, = self._branch_groups("branchAtMethodEnd")
        self.assertEqual({arm.terminus for arm in group.arms}, {"continues"})
        self.assertEqual(
            self._arm_call_names(group.id, "if")
            | self._arm_call_names(group.id, "else"),
            {"doX", "doInner"},
        )
        method_nodes = self._method_nodes("branchAtMethodEnd")
        self.assertTrue(any(
            node.type == "exit" and node.exitKind == "fallthrough"
            for node in method_nodes
        ))
        self.assertFalse(any(
            node.type == "call" and not node.branchArms
            for node in method_nodes
        ))

    def test_consecutive_throw_guards_keep_the_second_guard_on_the_normal_path(self):
        groups = sorted(
            self._branch_groups("consecutiveThrowGuards"),
            key=lambda group: group.line or -1,
        )
        self.assertEqual(len(groups), 2)
        second_head = next(
            arm.firstCallId
            for arm in groups[1].arms
            if arm.label == "if"
        )
        self.assertIsNotNone(second_head)
        self.assertTrue(groups[1].branchPointIds)
        self.assertTrue(all(
            self.by_id[point].calleeFullName != "<operator>.throw"
            for point in groups[1].branchPointIds
        ), "a later guard must never be anchored on an earlier terminal throw")
        self.assertTrue(
            self._sequence_reachable_without_terminals(
                "consecutiveThrowGuards", second_head,
            ),
            "the second guard must be reachable without traversing the first throw",
        )

    def test_consecutive_throw_guards_keep_the_common_continuation(self):
        helper = next(
            node for node in self._method_nodes("consecutiveThrowGuards")
            if node.type == "call"
            and _simple_name(node.calleeFullName or "") == "doHelper"
        )
        self.assertTrue(
            self._sequence_reachable_without_terminals(
                "consecutiveThrowGuards", helper.id,
            )
        )
        method_ids = {
            node.id for node in self._method_nodes("consecutiveThrowGuards")
        }
        terminal_ids = {
            node_id for node_id in method_ids
            if self.by_id[node_id].exitKind == "throw"
            or (
                self.by_id[node_id].type == "call"
                and self.by_id[node_id].calleeFullName == "<operator>.throw"
            )
        }
        self.assertFalse(any(
            edge.type == "sequence"
            and edge.source in terminal_ids
            and edge.target == helper.id
            for edge in self.graph.edges
        ))

    def test_unequal_distance_branch_keeps_each_path_nearest_call(self):
        group, = self._branch_groups("unequalDistanceBranch")
        self.assertEqual(self._arm_call_names(group.id, "if") & {"doX"}, {"doX"})
        self.assertEqual(self._arm_call_names(group.id, "else") & {"doY"}, {"doY"})
        for name in ("doX", "doY"):
            node = next(
                node for node in self._method_nodes("unequalDistanceBranch")
                if node.type == "call"
                and _simple_name(node.calleeFullName or "") == name
            )
            self.assertTrue(
                self._sequence_reachable_without_terminals(
                    "unequalDistanceBranch", node.id,
                ),
                f"the {name} arm was lost at a different CFG depth",
            )

    def test_throw_guard_keeps_assignment_only_normal_path_to_next_call(self):
        helper = next(
            node for node in self._method_nodes("normalPathAfterThrowGuard")
            if node.type == "call"
            and _simple_name(node.calleeFullName or "") == "doHelper"
        )
        self.assertTrue(
            self._sequence_reachable_without_terminals(
                "normalPathAfterThrowGuard", helper.id,
            )
        )

    def test_consecutive_empty_throw_and_return_branches_survive_filtering(self):
        method_name = "consecutiveEmptyTerminalBranches"
        raw_groups = sorted(
            self._branch_groups(method_name),
            key=lambda group: group.line or -1,
        )
        self.assertEqual(len(raw_groups), 2)
        first_throw = next(
            arm for arm in raw_groups[0].arms if arm.terminus == "throw"
        )
        second_return = next(
            arm for arm in raw_groups[1].arms if arm.terminus == "return"
        )
        self.assertFalse(any(
            node.type == "call"
            and not (node.calleeFullName or "").startswith("<operator>.")
            for node in self._arm_nodes(raw_groups[0].id, first_throw.label)
        ))
        self.assertFalse(any(
            node.type == "call"
            for node in self._arm_nodes(raw_groups[1].id, second_return.label)
        ))

        filtered_groups = sorted(
            (
                group for group in self.filtered.branchGroups
                if _simple_name(group.method or "") == method_name
            ),
            key=lambda group: group.line or -1,
        )
        self.assertEqual(len(filtered_groups), 2)
        filtered_throw = next(
            arm for arm in filtered_groups[0].arms if arm.terminus == "throw"
        )
        filtered_return = next(
            arm for arm in filtered_groups[1].arms if arm.terminus == "return"
        )
        self.assertTrue(filtered_throw.empty)
        self.assertIsNone(filtered_throw.firstCallId)
        self.assertTrue(filtered_return.empty)
        self.assertIsNone(filtered_return.firstCallId)
        self.assertTrue(filtered_groups[1].branchPointIds)

        definition = next(
            method
            for method in build_method_definitions(self.filtered).values()
            if _simple_name(method.methodFullName) == method_name
        )
        routed = prepare_method_branch_routes(definition)
        routed_groups = sorted(routed.branchGroups, key=lambda group: group.line or -1)
        self.assertTrue(next(
            arm for arm in routed_groups[0].arms if arm.terminus == "throw"
        ).empty)
        self.assertTrue(next(
            arm for arm in routed_groups[1].arms if arm.terminus == "return"
        ).empty)

        requirements_by_target = {
            edge.target: {
                (requirement.groupId, requirement.armLabel)
                for requirement in edge.branchRequirements
            }
            for edge in routed.sequenceEdges
            if edge.source == routed.entryId
        }
        return_id = next(
            node.id for node in routed.nodes if node.exitKind == "return"
        )
        helper_id = next(
            node.id for node in routed.nodes
            if _simple_name(node.calleeFullName or "") == "doHelper"
        )
        self.assertEqual(requirements_by_target[return_id], {
            (routed_groups[0].id, "else"),
            (routed_groups[1].id, "if"),
        })
        self.assertEqual(requirements_by_target[helper_id], {
            (routed_groups[0].id, "else"),
            (routed_groups[1].id, "else"),
        })

    def test_consecutive_empty_terminals_keep_only_the_normal_common_route(self):
        method_name = "consecutiveEmptyTerminalBranches"
        groups = sorted(
            self._branch_groups(method_name),
            key=lambda group: group.line or -1,
        )
        return_exit = next(
            node for node in self._method_nodes(method_name)
            if node.type == "exit"
            and node.exitKind == "return"
        )
        helper = next(
            node for node in self._method_nodes(method_name)
            if node.type == "call"
            and _simple_name(node.calleeFullName or "") == "doHelper"
        )
        self.assertTrue(
            self._sequence_reachable_without_terminals(method_name, return_exit.id)
        )
        self.assertTrue(
            self._sequence_reachable_without_terminals(method_name, helper.id)
        )
        throw_members = {
            node.id for node in self._arm_nodes(groups[0].id, "if")
            if node.exitKind == "throw"
            or node.calleeFullName == "<operator>.throw"
        }
        self.assertFalse(any(
            edge.type == "sequence"
            and edge.source in throw_members
            and edge.target in {return_exit.id, helper.id}
            for edge in self.graph.edges
        ))

    def test_return_helper_flows_from_call_to_explicit_return(self):
        pairs = self._method_sequence_pairs("returnHelper")
        self.assertTrue(any(
            _simple_name(source.calleeFullName or "") == "helper"
            and target.type == "exit"
            and target.exitKind == "return"
            for source, target in pairs
        ))

    def test_conditional_return_keeps_both_call_arms_and_no_fallthrough(self):
        nodes = self._method_nodes("returnConditional")
        calls = {
            _simple_name(node.calleeFullName or "")
            for node in nodes if node.type == "call"
        }
        exits = [node for node in nodes if node.type == "exit"]
        self.assertTrue({"helperA", "helperB"} <= calls)
        self.assertTrue(exits)
        self.assertEqual({node.exitKind for node in exits}, {"return"})

    def test_nested_return_preserves_argument_before_wrapper_order(self):
        pairs = self._method_sequence_pairs("returnWrapper")
        readable = {
            (
                _simple_name(source.calleeFullName or "") if source.type == "call" else source.type,
                _simple_name(target.calleeFullName or "") if target.type == "call" else target.exitKind,
            )
            for source, target in pairs
        }
        self.assertIn(("helper", "wrapper"), readable)
        self.assertIn(("wrapper", "return"), readable)

    def test_call_then_bare_return_has_no_fallthrough_exit(self):
        pairs = self._method_sequence_pairs("helperThenReturn")
        self.assertTrue(any(
            _simple_name(source.calleeFullName or "") == "helper"
            and target.type == "exit"
            and target.exitKind == "return"
            for source, target in pairs
        ))
        self.assertNotIn(
            "fallthrough",
            {node.exitKind for node in self._method_nodes("helperThenReturn")},
        )

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
        self.assertTrue(all(
            not self._arm_call_names(group.id, arm.label)
            for arm in group.arms
        ))
        returning_arm = next(arm for arm in group.arms if arm.terminus == "return")
        self.assertTrue(any(
            node.type == "exit" and node.exitKind == "return"
            for node in self._arm_nodes(group.id, returning_arm.label)
        ))
        self.assertEqual(len(group.branchPointIds), 1)
        anchor = self.by_id[group.branchPointIds[0]]
        self.assertEqual(_simple_name(anchor.calleeFullName or ""), "doInner")

        method_nodes = self._method_nodes("earlyReturn")
        return_ids = {
            node.id
            for node in method_nodes
            if node.type == "exit" and node.exitKind == "return"
        }
        subsequent_ids = {
            node.id
            for node in method_nodes
            if node.type == "call"
            and _simple_name(node.calleeFullName or "") == "doX"
        }
        self.assertTrue(return_ids)
        self.assertFalse(any(
            edge.type == "sequence"
            and edge.source in return_ids
            and edge.target in subsequent_ids
            for edge in self.graph.edges
        ))

    def test_zero_call_return_branch_anchors_on_its_condition_call(self):
        group = next(
            group for group in self.graph.branchGroups
            if _simple_name(group.method or "") == "callConditionReturn"
        )

        self.assertEqual([arm.terminus for arm in group.arms], ["return", "continues"])
        self.assertTrue(all(
            not self._arm_call_names(group.id, arm.label)
            for arm in group.arms
        ))
        self.assertEqual(len(group.branchPointIds), 1)
        anchor = self.by_id[group.branchPointIds[0]]
        self.assertEqual(_simple_name(anchor.calleeFullName or ""), "hasRole")

    def test_short_circuit_zero_call_return_uses_last_visible_condition_call(self):
        group = next(
            group for group in self.graph.branchGroups
            if _simple_name(group.method or "") == "shortCircuitCallConditionReturn"
        )

        self.assertEqual([arm.terminus for arm in group.arms], ["return", "continues"])
        self.assertTrue(all(
            not self._arm_call_names(group.id, arm.label)
            for arm in group.arms
        ))
        self.assertEqual(len(group.branchPointIds), 1)
        anchor = self.by_id[group.branchPointIds[0]]
        self.assertEqual(_simple_name(anchor.calleeFullName or ""), "isOwner")
        has_role = next(
            node for node in self._method_nodes("shortCircuitCallConditionReturn")
            if _simple_name(node.calleeFullName or "") == "hasRole"
        )
        is_owner = next(
            node for node in self._method_nodes("shortCircuitCallConditionReturn")
            if _simple_name(node.calleeFullName or "") == "isOwner"
        )
        self.assertTrue(any(
            edge.type == "sequence"
            and edge.source == has_role.id
            and edge.target == is_owner.id
            for edge in self.graph.edges
        ), "the false short-circuit path must reach the second condition call")

    def test_asymmetric_if_retains_the_first_call_from_each_arm(self):
        group = next(
            group for group in self.graph.branchGroups
            if _simple_name(group.method or "") == "asymmetricBranch"
        )
        heads = {
            arm.label: self.by_id[arm.firstCallId]
            for arm in group.arms
            if arm.firstCallId is not None
        }

        self.assertEqual(_simple_name(heads["if"].calleeFullName or ""), "doX")
        self.assertEqual(_simple_name(heads["else"].calleeFullName or ""), "valueOf")
        branch_edges = {
            (edge.source, edge.target)
            for edge in self.graph.edges
            if edge.type == "sequence"
        }
        for point in group.branchPointIds:
            self.assertIn((point, heads["if"].id), branch_edges)
            self.assertIn((point, heads["else"].id), branch_edges)

        root_id = next(
            node.id for node in self.graph.nodes
            if node.type == "entry" and _simple_name(node.calleeFullName or "") == "doA"
        )
        sliced = slice_from_root(self.graph, root_id)
        sliced_ids = {node.id for node in sliced.nodes}
        self.assertIn(heads["else"].id, sliced_ids)

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
        self.assertTrue({"doA", "doProcessTwo"} <= callers)

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
