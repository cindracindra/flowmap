from __future__ import annotations

import unittest

from fixture import SOURCE_DIR, start_fixture_session

from backend.src.flowmap.domain.cfg_filtering import filter_noise_cfg
from backend.src.flowmap.domain.method_scoping import build_method_definitions
from backend.src.flowmap.domain.method_structure_validation import (
    validate_all_method_structures,
)
from backend.src.flowmap.service.cfg import extract_cfg_structure

CLASS = "com.flowmap.fixture.OperationalChains"


class BranchCfgShapeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.session = start_fixture_session()
        cls.raw = extract_cfg_structure(cls.session, SOURCE_DIR)
        cls.filtered = filter_noise_cfg(cls.raw, preserve_all_entries=True)

    @classmethod
    def tearDownClass(cls):
        cls.session.stop()

    @staticmethod
    def method_name(name: str, parameters: str) -> str:
        return f"{CLASS}.{name}:void({parameters})"

    def test_filtered_methods_satisfy_authoritative_structure_contract(self):
        node_ids = {node.id for node in self.filtered.nodes}
        missing_exit_destinations = [
            (group.id, arm.label, exit_.kind, exit_.destinationNodeId)
            for group in self.filtered.branchGroups
            for arm in group.arms
            for exit_ in arm.exits
            if exit_.destinationNodeId is not None
            and exit_.destinationNodeId not in node_ids
        ]
        self.assertEqual(missing_exit_destinations, [])
        methods = build_method_definitions(self.filtered)

        self.assertIs(validate_all_method_structures(methods), methods)

    def test_every_fixture_method_satisfies_complete_end_to_end_topology(self):
        methods = build_method_definitions(self.filtered)

        for method in methods.values():
            with self.subTest(method=method.methodFullName):
                self.assert_complete_method_topology(method)

    def groups_for(self, method: str):
        return [group for group in self.filtered.branchGroups if group.method == method]

    def nodes_for(self, method: str):
        return [
            node for node in self.filtered.nodes
            if node.callerMethod == method or node.calleeFullName == method
        ]

    def decisions_for(self, method: str):
        return [
            node for node in self.nodes_for(method)
            if (
                node.type == "structure"
                and node.structureRole == "decision"
            )
        ]

    def assert_complete_method_topology(self, method):
        nodes = {
            method.entry.id: method.entry,
            **{node.id: node for node in method.nodes},
        }
        sequence = list(method.sequenceEdges)
        outgoing = {}
        incoming = {}
        for edge in sequence:
            self.assertIn(edge.source, nodes, (method.methodFullName, edge))
            self.assertIn(edge.target, nodes, (method.methodFullName, edge))
            outgoing.setdefault(edge.source, []).append(edge)
            incoming.setdefault(edge.target, []).append(edge)

        reached = set()
        pending = [method.entry.id]
        while pending:
            node_id = pending.pop()
            if node_id in reached:
                continue
            reached.add(node_id)
            pending.extend(edge.target for edge in outgoing.get(node_id, ()))
        self.assertEqual(
            set(nodes), reached,
            (method.methodFullName, "unreachable", sorted(set(nodes) - reached)),
        )

        valid_arms = {
            group.id: {arm.label for arm in group.arms}
            for group in method.branchGroups
        }
        for edge in sequence:
            requirements = {
                (item.groupId, item.armLabel)
                for item in edge.branchRequirements
            }
            self.assertEqual(
                len(requirements), len(edge.branchRequirements),
                (method.methodFullName, "duplicate requirements", edge),
            )
            for group_id, arm_label in requirements:
                self.assertIn(group_id, valid_arms, edge)
                self.assertIn(arm_label, valid_arms[group_id], edge)
            target_membership = {
                (item.groupId, item.armLabel)
                for item in nodes[edge.target].branchArms
            }
            self.assertLessEqual(target_membership, requirements, edge)

        terminal_ids = {
            node.id for node in nodes.values()
            if node.type == "exit"
            and node.exitKind in {"return", "throw", "fallthrough"}
        }
        self.assertFalse(any(
            edge.source in terminal_ids for edge in sequence
        ), (method.methodFullName, "terminal node continues"))

        for group in method.branchGroups:
            entry = nodes[group.entryNodeId]
            exit_node = nodes.get(group.exitNodeId) if group.exitNodeId else None
            enclosure = {
                (item.groupId, item.armLabel)
                for item in group.enclosingRequirements
            }
            self.assertEqual(
                {(item.groupId, item.armLabel) for item in entry.branchArms},
                enclosure,
                (method.methodFullName, group.id, "entry enclosure"),
            )
            if exit_node is not None:
                self.assertEqual(
                    {(item.groupId, item.armLabel) for item in exit_node.branchArms},
                    enclosure,
                    (method.methodFullName, group.id, "exit enclosure"),
                )

            decision_ids = {
                stage.decisionNodeId for stage in group.conditionStages
            }
            decision_ids.update(
                node.id for node in nodes.values()
                if node.type == "structure"
                and node.structureGroupId == group.id
                and node.structureRole == "decision"
            )
            condition_ids = {
                node_id
                for stage in group.conditionStages
                for node_id in stage.nodeIds
            }
            owned_ids = {
                node.id for node in nodes.values()
                if any(item.groupId == group.id for item in node.branchArms)
            }
            internal_ids = decision_ids | condition_ids | owned_ids
            region_ids = internal_ids | {group.entryNodeId}

            illegal_ingress = [
                edge for edge in sequence
                if edge.target in internal_ids
                and edge.source not in region_ids
                and not (
                    nodes[edge.target].type == "exit"
                    and nodes[edge.target].exitKind in {"return", "throw"}
                )
            ]
            if group.kind != "TRY":
                self.assertEqual(
                    illegal_ingress, [],
                    (method.methodFullName, group.id, "bypassed entry"),
                )
            self.assertTrue(
                outgoing.get(group.entryNodeId),
                (method.methodFullName, group.id, "entry has no route"),
            )

            for arm in group.arms:
                arm_ids = {
                    node.id for node in nodes.values()
                    if any(
                        item.groupId == group.id and item.armLabel == arm.label
                        for item in node.branchArms
                    )
                }

                illegal_egress = []
                for source in arm_ids:
                    for edge in outgoing.get(source, ()):
                        target = nodes[edge.target]
                        target_in_arm = any(
                            item.groupId == group.id and item.armLabel == arm.label
                            for item in target.branchArms
                        )
                        if target_in_arm:
                            continue
                        if group.exitNodeId is not None and edge.target == group.exitNodeId:
                            continue
                        if nodes[source].type == "transfer":
                            continue
                        if target.type == "exit" and target.exitKind in {"return", "throw"}:
                            continue
                        illegal_egress.append(edge)
                if group.kind != "TRY":
                    self.assertEqual(
                        illegal_egress, [],
                        (method.methodFullName, group.id, arm.label, "arm bypass"),
                    )

                for arm_exit in arm.exits:
                    self.assertIsNotNone(
                        arm_exit.destinationNodeId,
                        (method.methodFullName, group.id, arm.label, arm_exit.kind),
                    )
                    if arm_exit.kind == "continues" and group.kind != "TRY":
                        self.assertEqual(
                            arm_exit.destinationNodeId, group.exitNodeId,
                            (method.methodFullName, group.id, arm.label),
                        )
                        self.assertTrue(any(
                            edge.target == group.exitNodeId
                            and (group.id, arm.label) in {
                                (item.groupId, item.armLabel)
                                for item in edge.branchRequirements
                            }
                            for edge in sequence
                        ), (method.methodFullName, group.id, arm.label, "no convergence"))

            if exit_node is not None:
                for edge in outgoing.get(exit_node.id, ()):
                    self.assertFalse(any(
                        item.groupId == group.id
                        for item in nodes[edge.target].branchArms
                    ), (method.methodFullName, group.id, "exit re-enters group", edge))
                    self.assertFalse(any(
                        item.groupId == group.id
                        for item in edge.branchRequirements
                    ), (method.methodFullName, group.id, "exit retains requirement", edge))

        for loop in method.loopGroups:
            entry = nodes[loop.entryNodeId]
            exit_node = nodes[loop.exitNodeId]
            loop_owned = {
                node.id for node in nodes.values() if loop.id in node.loopIds
            }
            self.assertNotIn(loop.id, entry.loopIds)
            self.assertNotIn(loop.id, exit_node.loopIds)
            self.assertTrue(outgoing.get(entry.id), (method.methodFullName, loop.id))

            illegal_ingress = [
                edge for edge in sequence
                if edge.target in loop_owned
                and edge.source not in loop_owned
                and edge.source != entry.id
            ]
            self.assertEqual(
                illegal_ingress, [],
                (method.methodFullName, loop.id, "bypassed loop entry"),
            )
            illegal_egress = [
                edge for source in loop_owned
                for edge in outgoing.get(source, ())
                if edge.target not in loop_owned and edge.target != exit_node.id
            ]
            self.assertEqual(
                illegal_egress, [],
                (method.methodFullName, loop.id, "bypassed loop exit"),
            )
            for edge in outgoing.get(exit_node.id, ()):
                self.assertNotIn(
                    loop.id, nodes[edge.target].loopIds,
                    (method.methodFullName, loop.id, "exit re-enters loop", edge),
                )

    def assert_direct_call_chain(self, method: str, call_names: list[str]):
        method_nodes = self.nodes_for(method)
        node_ids = []
        for name in call_names:
            matches = [
                node.id for node in method_nodes
                if node.type == "call"
                and node.calleeFullName is not None
                and f".{name}:" in node.calleeFullName
            ]
            self.assertEqual(len(matches), 1, (method, name, matches))
            node_ids.append(matches[0])
        sequence = {
            (edge.source, edge.target)
            for edge in self.filtered.edges
            if edge.type == "sequence"
        }
        self.assertTrue(
            all(pair in sequence for pair in zip(node_ids, node_ids[1:])),
            (method, node_ids),
        )
        return node_ids

    def test_inline_arguments_preserve_java_execution_order(self):
        simple = self.method_name("inlineArgumentOrderShape", "")
        self.assert_direct_call_chain(
            simple, ["helperA", "helperB", "wrapperTwo"]
        )

        nested = self.method_name("nestedInlineArgumentOrderShape", "")
        self.assert_direct_call_chain(
            nested, ["helperA", "wrapper", "helperB", "wrapperTwo"]
        )

    def test_inline_arguments_after_loop_follow_loop_exit_in_order(self):
        method = self.method_name("inlineOrderAfterLoopShape", "boolean")
        helper_a, _, _ = self.assert_direct_call_chain(
            method, ["helperA", "helperB", "wrapperTwo"]
        )
        loop = next(
            group for group in self.filtered.loopGroups if group.method == method
        )
        self.assertIn(
            (loop.exitNodeId, helper_a),
            {
                (edge.source, edge.target)
                for edge in self.filtered.edges
                if edge.type == "sequence"
            },
        )

    def test_and_or_conditions_use_one_complete_non_short_circuit_chain(self):
        expected_operator = {
            "shortCircuitCallConditionReturn": "<operator>.logicalOr",
            "shortCircuitAndConditionReturn": "<operator>.logicalAnd",
        }
        raw_nodes = {node.id: node for node in self.raw.nodes}
        raw_sequence = {
            (edge.source, edge.target)
            for edge in self.raw.edges
            if edge.type == "sequence"
        }

        for method_base, operator in expected_operator.items():
            with self.subTest(operator=operator):
                method = self.method_name(method_base, "")
                group = next(
                    group for group in self.raw.branchGroups
                    if group.method == method
                )
                stage = group.conditionStages[0]
                callees = [raw_nodes[node_id].calleeFullName for node_id in stage.nodeIds]

                self.assertEqual(
                    callees,
                    [
                        f"{CLASS}.hasRole:boolean()",
                        f"{CLASS}.isOwner:boolean()",
                        operator,
                    ],
                )
                expected_chain = list(zip(stage.nodeIds, stage.nodeIds[1:]))
                self.assertTrue(all(edge in raw_sequence for edge in expected_chain))
                self.assertIn(
                    (stage.nodeIds[-1], stage.decisionNodeId), raw_sequence
                )
                self.assertNotIn(
                    (stage.nodeIds[0], stage.nodeIds[-1]), raw_sequence
                )

                filtered_nodes = {
                    node.id: node for node in self.nodes_for(method)
                }
                filtered_sequence = {
                    (edge.source, edge.target)
                    for edge in self.filtered.edges
                    if edge.type == "sequence"
                }
                left = next(
                    node.id for node in filtered_nodes.values()
                    if node.calleeFullName == f"{CLASS}.hasRole:boolean()"
                )
                right = next(
                    node.id for node in filtered_nodes.values()
                    if node.calleeFullName == f"{CLASS}.isOwner:boolean()"
                )
                decision = group.conditionStages[0].decisionNodeId
                self.assertIn((left, right), filtered_sequence)
                self.assertIn((right, decision), filtered_sequence)
                self.assertNotIn((left, decision), filtered_sequence)

    def test_nested_identifier_decisions_keep_distinct_outer_arm_membership(self):
        method = self.method_name(
            "nestedIdentifierBranches", "boolean,boolean,boolean"
        )
        groups = self.groups_for(method)
        outer = min(groups, key=lambda group: group.line or 0)
        nested = [group for group in groups if group.id != outer.id]

        self.assertEqual(len(groups), 3)
        self.assertEqual(len(nested), 2)
        self.assertEqual(
            {
                tuple((item.groupId, item.armLabel) for item in group.enclosingRequirements)
                for group in nested
            },
            {((outer.id, "if"),), ((outer.id, "elseif1"),)},
        )

        decisions = {
            node.structureGroupId: node
            for node in self.decisions_for(method)
        }
        for group in nested:
            self.assertEqual(
                [(item.groupId, item.armLabel) for item in decisions[group.id].branchArms],
                [(item.groupId, item.armLabel) for item in group.enclosingRequirements],
            )

    def test_nested_decisions_are_not_unconditional_method_entry_children(self):
        method = self.method_name(
            "nestedIdentifierBranches", "boolean,boolean,boolean"
        )
        nodes = self.nodes_for(method)
        entry = next(node for node in nodes if node.type == "entry")
        nested_ids = {
            node.id for node in nodes
            if node.structureRole == "decision" and node.branchArms
        }

        self.assertTrue(nested_ids)
        self.assertFalse(any(
            edge.type == "sequence"
            and edge.source == entry.id
            and edge.target in nested_ids
            for edge in self.filtered.edges
        ))

    def test_visible_predecessor_cannot_bypass_call_free_nested_branch(self):
        method = self.method_name(
            "visibleCallBeforeCallFreeNestedBranchShape", "boolean,boolean"
        )
        nodes = self.nodes_for(method)
        node_ids = {node.id for node in nodes}
        groups = self.groups_for(method)
        outer = next(group for group in groups if not group.enclosingRequirements)
        inner = next(group for group in groups if group.id != outer.id)
        predecessor = next(
            node for node in nodes
            if node.type == "call" and node.code == "this.doX()"
        )
        edges = [
            edge for edge in self.filtered.edges
            if edge.type == "sequence"
            and edge.source in node_ids
            and edge.target in node_ids
        ]

        self.assertTrue(any(
            edge.source == predecessor.id and edge.target == inner.entryNodeId
            for edge in edges
        ))
        self.assertFalse(any(
            edge.source == predecessor.id and edge.target == outer.exitNodeId
            for edge in edges
        ), "the retained predecessor must not bypass the nested branch")

    def test_consecutive_identifier_guards_route_through_both_decisions(self):
        method = self.method_name(
            "consecutiveIdentifierBranches", "boolean,boolean"
        )
        groups = sorted(self.groups_for(method), key=lambda group: group.line or 0)
        decisions = sorted(
            self.decisions_for(method),
            key=lambda node: node.line or 0,
        )

        self.assertEqual(len(groups), 2)
        self.assertEqual(len(decisions), 2)
        self.assertTrue(any(
            edge.type == "sequence"
            and edge.source == decisions[0].id
            and edge.target == groups[0].exitNodeId
            and {(item.groupId, item.armLabel) for item in edge.branchRequirements}
                == {(groups[0].id, "else")}
            for edge in self.filtered.edges
        ))
        self.assertTrue(any(
            edge.type == "sequence"
            and edge.source == groups[0].exitNodeId
            and edge.target == groups[1].entryNodeId
            and not edge.branchRequirements
            for edge in self.filtered.edges
        ))

    def test_if_groups_emit_entry_stage_decisions_and_exit_anchors(self):
        method = self.method_name(
            "nestedIdentifierBranches", "boolean,boolean,boolean"
        )
        nodes = {node.id: node for node in self.nodes_for(method)}
        for group in self.groups_for(method):
            self.assertIsNotNone(group.entryNodeId)
            self.assertIn(group.entryNodeId, nodes)
            self.assertEqual(nodes[group.entryNodeId].structureRole, "entry")
            self.assertTrue(group.conditionStages)
            decision_ids = [
                stage.decisionNodeId for stage in group.conditionStages
            ]
            for stage in group.conditionStages:
                self.assertIn(stage.decisionNodeId, nodes)
                self.assertEqual(
                    nodes[stage.decisionNodeId].structureRole, "decision"
                )
            if group.exitNodeId is not None:
                self.assertIn(group.exitNodeId, nodes)
                self.assertEqual(nodes[group.exitNodeId].structureRole, "exit")
            for arm in group.arms:
                self.assertTrue(arm.exits, (group.id, arm.label))
                for exit_ in arm.exits:
                    self.assertIsNotNone(
                        exit_.destinationNodeId,
                        (group.id, arm.label, exit_.kind),
                    )
                    if exit_.kind == "continues":
                        self.assertEqual(
                            exit_.destinationNodeId, group.exitNodeId
                        )

    def test_every_decision_input_includes_its_lexical_enclosure(self):
        decision_nodes = {
            node.id: node for node in self.filtered.nodes
            if node.type == "structure" and node.structureRole == "decision"
        }
        for decision_id, decision in decision_nodes.items():
            expected = {
                (item.groupId, item.armLabel) for item in decision.branchArms
            }
            incoming = [
                edge for edge in self.filtered.edges
                if edge.type == "sequence" and edge.target == decision_id
            ]
            self.assertTrue(incoming, decision_id)
            for edge in incoming:
                actual = {
                    (item.groupId, item.armLabel)
                    for item in edge.branchRequirements
                }
                self.assertLessEqual(expected, actual, (decision_id, edge))

    def test_explicit_return_never_flows_to_method_fallthrough(self):
        nodes = {node.id: node for node in self.raw.nodes}
        for edge in self.raw.edges:
            if edge.type != "sequence":
                continue
            source = nodes.get(edge.source)
            target = nodes.get(edge.target)
            self.assertFalse(
                source is not None
                and source.type == "exit"
                and source.exitKind == "return"
                and target is not None
                and target.type == "exit"
                and target.exitKind == "fallthrough",
                edge,
            )

    def test_method_terminal_exits_have_no_outgoing_sequence_edges(self):
        exit_ids = {
            node.id for node in self.raw.nodes
            if node.type == "exit"
            and node.exitKind in {"return", "throw", "fallthrough"}
        }
        self.assertFalse(any(
            edge.type == "sequence" and edge.source in exit_ids
            for edge in self.raw.edges
        ))

    def test_branching_methods_have_no_entry_to_fallthrough_shortcut(self):
        branching_methods = {
            group.method for group in self.filtered.branchGroups
            if group.method is not None
        }
        entries = {
            node.calleeFullName: node
            for node in self.filtered.nodes
            if node.type == "entry" and node.calleeFullName is not None
        }
        fallthrough_by_method = {
            node.callerMethod: node.id
            for node in self.filtered.nodes
            if node.type == "exit" and node.exitKind == "fallthrough"
        }
        for method in branching_methods:
            entry = entries.get(method)
            fallthrough_id = fallthrough_by_method.get(method)
            if entry is None or fallthrough_id is None:
                continue
            self.assertFalse(any(
                edge.type == "sequence"
                and edge.source == entry.id
                and edge.target == fallthrough_id
                for edge in self.filtered.edges
            ), method)

    def test_empty_continuing_arms_have_real_non_self_targets(self):
        for group in self.filtered.branchGroups:
            for arm in group.arms:
                for exit_ in arm.exits:
                    if not arm.empty or exit_.kind != "continues":
                        continue
                    self.assertIsNotNone(
                        exit_.destinationNodeId, (group.id, arm.label)
                    )
                    self.assertNotIn(
                        exit_.destinationNodeId,
                        [stage.decisionNodeId for stage in group.conditionStages],
                        (group.id, arm.label, exit_.destinationNodeId),
                    )

    def test_basic_loop_kinds_are_single_iteration_acyclic_regions(self):
        cases = {
            "whileLoopShape": ("boolean", "WHILE", False),
            "doWhileLoopShape": ("boolean", "DO", False),
            "forLoopShape": ("int", "FOR", False),
            "enhancedForLoopShape": ("java.util.List", "FOR_EACH", False),
        }
        for name, (parameters, expected_kind, permits_zero_iterations) in cases.items():
            with self.subTest(loop=name):
                method = self.method_name(name, parameters)
                loops = [
                    loop for loop in self.filtered.loopGroups
                    if loop.method == method
                ]
                self.assertEqual(len(loops), 1)
                self.assertEqual(loops[0].kind, expected_kind)
                nodes = self.nodes_for(method)
                self.assertIsNotNone(loops[0].entryNodeId)
                self.assertIsNotNone(loops[0].exitNodeId)
                loop_entry = next(
                    node for node in nodes if node.id == loops[0].entryNodeId
                )
                loop_exit = next(
                    node for node in nodes if node.id == loops[0].exitNodeId
                )
                self.assertEqual(loop_entry.structureRole, "entry")
                self.assertEqual(loop_exit.structureRole, "exit")
                self.assertNotIn(loops[0].id, loop_entry.loopIds)
                self.assertNotIn(loops[0].id, loop_exit.loopIds)
                entry = next(node for node in nodes if node.type == "entry")
                body = next(
                    node for node in nodes
                    if node.type == "call" and node.code == "this.doInner()"
                )
                after = next(
                    node for node in nodes
                    if node.type == "call" and node.code == "this.doX()"
                )
                self.assertIn(loops[0].id, body.loopIds)
                self.assertNotIn(loops[0].id, after.loopIds)

                node_ids = {node.id for node in nodes}
                edges = [
                    edge for edge in self.filtered.edges
                    if edge.type == "sequence"
                    and edge.source in node_ids
                    and edge.target in node_ids
                ]
                self.assertTrue(any(
                    edge.source == loop_exit.id and edge.target == after.id
                    for edge in edges
                ))
                self.assertTrue(any(
                    edge.target == loop_exit.id for edge in edges
                ))
                self.assertEqual(
                    any(edge.source == entry.id and edge.target == after.id for edge in edges),
                    permits_zero_iterations,
                )

                outgoing = {}
                for edge in edges:
                    outgoing.setdefault(edge.source, []).append(edge.target)
                visiting = set()
                visited = set()

                def visit(node_id):
                    if node_id in visiting:
                        return False
                    if node_id in visited:
                        return True
                    visiting.add(node_id)
                    if not all(visit(target) for target in outgoing.get(node_id, ())):
                        return False
                    visiting.remove(node_id)
                    visited.add(node_id)
                    return True

                self.assertTrue(visit(entry.id), (name, edges))

    def test_loop_with_branch_exits_after_one_iteration_not_back_to_decision(self):
        method = self.method_name("loopWithBranchShape", "boolean,boolean")
        nodes = self.nodes_for(method)
        node_ids = {node.id for node in nodes}
        decision = next(node for node in self.decisions_for(method))
        after = next(
            node for node in nodes
            if node.type == "call" and node.code == "this.doX()"
        )
        tails = {
            node.id for node in nodes
            if node.type == "call"
            and node.code in {"this.doInner()", "this.doHelper()"}
        }
        loop = next(loop for loop in self.filtered.loopGroups if loop.method == method)
        edges = [
            edge for edge in self.filtered.edges
            if edge.type == "sequence"
            and edge.source in node_ids
            and edge.target in node_ids
        ]

        self.assertEqual(len(tails), 2)
        self.assertIn(
            loop.id,
            decision.loopIds,
            "structural decisions inside a loop carry lexical loop ownership",
        )
        group = next(group for group in self.groups_for(method))
        self.assertIsNotNone(group.exitNodeId)
        for tail in tails:
            self.assertTrue(any(
                edge.source == tail and edge.target == group.exitNodeId
                for edge in edges
            ))
            self.assertFalse(any(
                edge.source == tail and edge.target == decision.id for edge in edges
            ))
            self.assertFalse(any(
                edge.source == tail and edge.target == after.id for edge in edges
            ))
        self.assertTrue(any(
            edge.source == group.exitNodeId and edge.target == loop.exitNodeId
            for edge in edges
        ))
        self.assertTrue(any(
            edge.source == loop.exitNodeId and edge.target == after.id
            for edge in edges
        ))

    def test_for_loop_with_two_updates_does_not_use_update_as_continuation(self):
        method = self.method_name(
            "forLoopWithTwoUpdatesAndBreakShape",
            "java.lang.String,java.lang.String,int",
        )
        nodes = [
            node for node in self.raw.nodes
            if node.callerMethod == method or node.calleeFullName == method
        ]
        node_ids = {node.id for node in nodes}
        loop = next(loop for loop in self.raw.loopGroups if loop.method == method)
        updates = {
            node.id for node in nodes
            if node.type == "call"
            and node.code in {"actualSuffix--", "expectedSuffix--"}
        }
        after = next(
            node for node in nodes
            if node.type == "call" and node.code == "this.doX()"
        )
        edges = [
            edge for edge in self.raw.edges
            if edge.type == "sequence"
            and edge.source in node_ids
            and edge.target in node_ids
        ]

        self.assertEqual(len(updates), 2)
        self.assertTrue(all(loop.id in node.loopIds for node in nodes if node.id in updates))
        self.assertTrue(any(
            edge.source in updates and edge.target == loop.exitNodeId
            for edge in edges
        ))
        self.assertTrue(any(
            edge.source == loop.exitNodeId and edge.target == after.id
            for edge in edges
        ))
        self.assertFalse(any(
            edge.source == loop.exitNodeId and edge.target in updates
            for edge in edges
        ))

    def test_branch_followed_by_loop_body_work_converges_before_that_work(self):
        method = self.method_name(
            "loopWithBranchThenWorkShape", "boolean,boolean"
        )
        nodes = self.nodes_for(method)
        node_ids = {node.id for node in nodes}
        group = next(group for group in self.groups_for(method))
        loop = next(loop for loop in self.filtered.loopGroups if loop.method == method)
        tails = {
            node.id for node in nodes
            if node.type == "call"
            and node.code in {"this.doInner()", "this.doHelper()"}
        }
        remaining = next(
            node for node in nodes
            if node.type == "call" and node.code == "this.doY()"
        )
        edges = [
            edge for edge in self.filtered.edges
            if edge.type == "sequence"
            and edge.source in node_ids
            and edge.target in node_ids
        ]

        self.assertEqual(len(tails), 2)
        for tail in tails:
            self.assertTrue(any(
                edge.source == tail and edge.target == group.exitNodeId
                for edge in edges
            ))
            self.assertFalse(any(
                edge.source == tail and edge.target == remaining.id
                for edge in edges
            ))
        self.assertTrue(any(
            edge.source == group.exitNodeId and edge.target == remaining.id
            for edge in edges
        ))
        self.assertTrue(any(
            edge.source == remaining.id and edge.target == loop.exitNodeId
            for edge in edges
        ))

    def test_nested_branch_tail_converges_child_then_parent(self):
        method = self.method_name(
            "loopWithNestedBranchTailShape", "boolean,boolean,boolean"
        )
        nodes = self.nodes_for(method)
        node_ids = {node.id for node in nodes}
        groups = self.groups_for(method)
        outer = next(group for group in groups if not group.enclosingRequirements)
        inner = next(group for group in groups if group.id != outer.id)
        loop = next(loop for loop in self.filtered.loopGroups if loop.method == method)
        after = next(
            node for node in nodes
            if node.type == "call" and node.code == "this.doX()"
        )
        inner_tails = {
            node.id for node in nodes
            if node.type == "call"
            and node.code in {"this.doInner()", "this.doHelper()"}
        }
        outer_else_tail = next(
            node.id for node in nodes
            if node.type == "call" and node.code == "this.doY()"
        )
        edges = [
            edge for edge in self.filtered.edges
            if edge.type == "sequence"
            and edge.source in node_ids
            and edge.target in node_ids
        ]

        for tail in inner_tails:
            self.assertTrue(any(
                edge.source == tail and edge.target == inner.exitNodeId
                and {
                    (item.groupId, item.armLabel)
                    for item in edge.branchRequirements
                } in (
                    {(outer.id, "if"), (inner.id, "if")},
                    {(outer.id, "if"), (inner.id, "else")},
                )
                for edge in edges
            ))
        self.assertTrue(any(
            edge.source == inner.exitNodeId and edge.target == outer.exitNodeId
            and {
                (item.groupId, item.armLabel)
                for item in edge.branchRequirements
            } == {(outer.id, "if")}
            for edge in edges
        ))
        self.assertTrue(any(
            edge.source == outer_else_tail and edge.target == outer.exitNodeId
            and {
                (item.groupId, item.armLabel)
                for item in edge.branchRequirements
            } == {(outer.id, "else")}
            for edge in edges
        ))
        self.assertTrue(any(
            edge.source == outer.exitNodeId and edge.target == loop.exitNodeId
            and not edge.branchRequirements
            for edge in edges
        ))
        self.assertTrue(any(
            edge.source == loop.exitNodeId and edge.target == after.id
            and not edge.branchRequirements
            for edge in edges
        ))
        self.assertFalse(any(
            edge.source in inner_tails and edge.target == outer.exitNodeId
            for edge in edges
        ))

    def test_enhanced_for_body_remains_reachable_after_preheader_filtering(self):
        method = self.method_name(
            "enhancedForWithFilteredPreheaderShape", "java.lang.String[]"
        )
        loops = [loop for loop in self.filtered.loopGroups if loop.method == method]
        nodes = self.nodes_for(method)
        node_ids = {node.id for node in nodes}
        entry = next(node for node in nodes if node.type == "entry")
        body = next(
            node for node in nodes
            if node.type == "call" and node.code == "value.trim()"
        )
        after = next(
            node for node in nodes
            if node.type == "call" and node.code == "this.doX()"
        )
        edges = [
            edge for edge in self.filtered.edges
            if edge.type == "sequence"
            and edge.source in node_ids
            and edge.target in node_ids
        ]
        outgoing = {}
        for edge in edges:
            outgoing.setdefault(edge.source, []).append(edge.target)

        reached = set()
        pending = [entry.id]
        while pending:
            node_id = pending.pop()
            if node_id in reached:
                continue
            reached.add(node_id)
            pending.extend(outgoing.get(node_id, ()))

        self.assertEqual(len(loops), 1)

        self.assertEqual(loops[0].kind, "FOR")
        self.assertIn(loops[0].id, body.loopIds)
        self.assertIn(body.id, reached)
        self.assertIn(after.id, reached)
        self.assertTrue(any(
            edge.source == body.id and edge.target == loops[0].exitNodeId
            for edge in edges
        ))
        self.assertTrue(any(
            edge.source == loops[0].exitNodeId and edge.target == after.id
            for edge in edges
        ))
        self.assertFalse(any(
            edge.source == entry.id and edge.target == after.id for edge in edges
        ))

    def test_conventional_for_preserves_executable_update_before_loop_exit(self):
        method = self.method_name("forLoopWithUpdateCallShape", "int")
        loop = next(loop for loop in self.filtered.loopGroups if loop.method == method)
        nodes = self.nodes_for(method)
        node_ids = {node.id for node in nodes}
        body = next(
            node for node in nodes
            if node.type == "call" and node.code == "this.doInner()"
        )
        update = next(
            node for node in nodes
            if node.type == "call" and node.code == "this.advance(index)"
        )
        edges = [
            edge for edge in self.filtered.edges
            if edge.type == "sequence"
            and edge.source in node_ids
            and edge.target in node_ids
        ]

        self.assertIn(loop.id, update.loopIds)
        self.assertTrue(any(
            edge.source == body.id and edge.target == update.id for edge in edges
        ))
        self.assertTrue(any(
            edge.source == update.id and edge.target == loop.exitNodeId
            for edge in edges
        ))

    def test_nested_and_consecutive_loop_anchors_compose(self):
        nested_method = self.method_name("nestedLoopShape", "boolean,boolean")
        nested_loops = [
            loop for loop in self.filtered.loopGroups
            if loop.method == nested_method
        ]
        nested_nodes = self.nodes_for(nested_method)
        nested_ids = {node.id for node in nested_nodes}
        nested_edges = [
            edge for edge in self.filtered.edges
            if edge.type == "sequence"
            and edge.source in nested_ids
            and edge.target in nested_ids
        ]
        outer = next(
            loop for loop in nested_loops
            if not next(
                node for node in nested_nodes if node.id == loop.entryNodeId
            ).loopIds
        )
        inner = next(loop for loop in nested_loops if loop.id != outer.id)
        inner_entry = next(
            node for node in nested_nodes if node.id == inner.entryNodeId
        )
        inner_exit = next(
            node for node in nested_nodes if node.id == inner.exitNodeId
        )
        remaining = next(
            node for node in nested_nodes
            if node.type == "call" and node.code == "this.doY()"
        )

        self.assertEqual(inner_entry.loopIds, [outer.id])
        self.assertEqual(inner_exit.loopIds, [outer.id])
        self.assertTrue(any(
            edge.source == inner.exitNodeId and edge.target == remaining.id
            for edge in nested_edges
        ))
        self.assertTrue(any(
            edge.source == remaining.id and edge.target == outer.exitNodeId
            for edge in nested_edges
        ))

        consecutive_method = self.method_name(
            "consecutiveLoopShape", "boolean,boolean"
        )
        consecutive_loops = sorted(
            (
                loop for loop in self.filtered.loopGroups
                if loop.method == consecutive_method
            ),
            key=lambda loop: loop.line or 0,
        )
        consecutive_nodes = self.nodes_for(consecutive_method)
        consecutive_ids = {node.id for node in consecutive_nodes}
        consecutive_edges = [
            edge for edge in self.filtered.edges
            if edge.type == "sequence"
            and edge.source in consecutive_ids
            and edge.target in consecutive_ids
        ]

        self.assertEqual(len(consecutive_loops), 2)
        self.assertTrue(any(
            edge.source == consecutive_loops[0].exitNodeId
            and edge.target == consecutive_loops[1].entryNodeId
            for edge in consecutive_edges
        ))

    def test_branch_and_loop_anchors_compose_in_both_orders_and_when_nested(self):
        branch_then_loop = self.method_name(
            "branchThenLoopShape", "boolean,boolean"
        )
        first_group = next(
            group for group in self.filtered.branchGroups
            if group.method == branch_then_loop
        )
        first_loop = next(
            loop for loop in self.filtered.loopGroups
            if loop.method == branch_then_loop
        )
        self.assertTrue(any(
            edge.type == "sequence"
            and edge.source == first_group.exitNodeId
            and edge.target == first_loop.entryNodeId
            for edge in self.filtered.edges
        ))

        loop_then_branch = self.method_name(
            "loopThenBranchShape", "boolean,boolean"
        )
        second_group = next(
            group for group in self.filtered.branchGroups
            if group.method == loop_then_branch
        )
        second_loop = next(
            loop for loop in self.filtered.loopGroups
            if loop.method == loop_then_branch
        )
        self.assertTrue(any(
            edge.type == "sequence"
            and edge.source == second_loop.exitNodeId
            and edge.target == second_group.entryNodeId
            for edge in self.filtered.edges
        ))

        nested_method = self.method_name(
            "branchContainingLoopShape", "boolean,boolean"
        )
        nested_group = next(
            group for group in self.filtered.branchGroups
            if group.method == nested_method
        )
        nested_loop = next(
            loop for loop in self.filtered.loopGroups
            if loop.method == nested_method
        )
        nested_nodes = {node.id: node for node in self.nodes_for(nested_method)}
        loop_entry = nested_nodes[nested_loop.entryNodeId]
        loop_exit = nested_nodes[nested_loop.exitNodeId]
        self.assertEqual(
            [(ref.groupId, ref.armLabel) for ref in loop_entry.branchArms],
            [(nested_group.id, "if")],
        )
        self.assertEqual(loop_exit.branchArms, loop_entry.branchArms)
        self.assertTrue(any(
            edge.type == "sequence"
            and edge.source == nested_loop.exitNodeId
            and edge.target == nested_group.exitNodeId
            for edge in self.filtered.edges
        ))

    def test_do_while_keeps_first_guard_evaluation_but_removes_guard_back_edge(self):
        method = self.method_name("doWhileCallConditionShape", "")
        loop = next(loop for loop in self.filtered.loopGroups if loop.method == method)
        nodes = self.nodes_for(method)
        node_ids = {node.id for node in nodes}
        body = next(
            node for node in nodes
            if node.type == "call" and node.code == "this.doInner()"
        )
        guard = next(
            node for node in nodes
            if node.type == "call" and node.code == "this.hasRole()"
        )
        after = next(
            node for node in nodes
            if node.type == "call" and node.code == "this.doX()"
        )
        edges = [
            edge for edge in self.filtered.edges
            if edge.type == "sequence"
            and edge.source in node_ids
            and edge.target in node_ids
        ]

        self.assertTrue(any(
            edge.source == body.id and edge.target == guard.id for edge in edges
        ))
        self.assertIn(
            loop.id,
            guard.loopIds,
            "loop guard calls are internal loop members, not preheader calls",
        )
        self.assertTrue(any(
            edge.source == guard.id and edge.target == loop.exitNodeId for edge in edges
        ))
        self.assertTrue(any(
            edge.source == loop.exitNodeId and edge.target == after.id for edge in edges
        ))
        self.assertFalse(any(
            edge.source == guard.id and edge.target == body.id for edge in edges
        ))

    def test_break_is_a_terminal_structural_exit_inside_its_branch_arm(self):
        method = self.method_name("loopWithBreakShape", "boolean,boolean")
        nodes = self.nodes_for(method)
        node_ids = {node.id for node in nodes}
        decision = next(node for node in self.decisions_for(method))
        break_node = next(
            node for node in nodes
            if node.type == "transfer" and node.transferKind == "break"
        )
        body = next(
            node for node in nodes
            if node.type == "call" and node.code == "this.doInner()"
        )
        after = next(
            node for node in nodes
            if node.type == "call" and node.code == "this.doX()"
        )
        edges = [
            edge for edge in self.filtered.edges
            if edge.type == "sequence"
            and edge.source in node_ids
            and edge.target in node_ids
        ]
        group = next(group for group in self.groups_for(method))
        loop = next(loop for loop in self.filtered.loopGroups if loop.method == method)
        break_arm = next(arm for arm in group.arms if arm.label == "if")

        self.assertEqual(
            [(item.groupId, item.armLabel) for item in break_node.branchArms],
            [(group.id, "if")],
        )
        self.assertIn(loop.id, break_node.loopIds)
        self.assertEqual(break_node.targetStructureGroupId, loop.id)
        self.assertEqual([exit_.kind for exit_ in break_arm.exits], ["break"])
        self.assertTrue(any(
            edge.source == decision.id
            and edge.target == break_node.id
            and {(item.groupId, item.armLabel) for item in edge.branchRequirements}
                == {(group.id, "if")}
            for edge in edges
        ))
        break_routes = [edge for edge in edges if edge.source == break_node.id]
        self.assertEqual(len(break_routes), 1)
        self.assertEqual(break_routes[0].target, loop.exitNodeId)
        self.assertEqual(
            {(item.groupId, item.armLabel)
             for item in break_routes[0].branchRequirements},
            {(group.id, "if")},
        )
        self.assertNotEqual(break_routes[0].target, group.exitNodeId)
        self.assertTrue(any(
            edge.source == decision.id and edge.target == group.exitNodeId
            and {(item.groupId, item.armLabel) for item in edge.branchRequirements}
                == {(group.id, "else")}
            for edge in edges
        ))
        self.assertTrue(any(
            edge.source == group.exitNodeId and edge.target == body.id
            for edge in edges
        ))
        self.assertTrue(any(
            edge.source == body.id and edge.target == loop.exitNodeId
            for edge in edges
        ))
        self.assertTrue(any(
            edge.source == loop.exitNodeId and edge.target == after.id
            for edge in edges
        ))

    def test_continue_is_a_dedicated_transfer_directly_to_loop_exit(self):
        method = self.method_name("loopWithContinueShape", "boolean,boolean")
        nodes = self.nodes_for(method)
        node_ids = {node.id for node in nodes}
        continue_node = next(
            node for node in nodes
            if node.type == "transfer" and node.transferKind == "continue"
        )
        group = next(group for group in self.groups_for(method))
        loop = next(loop for loop in self.filtered.loopGroups if loop.method == method)
        edges = [
            edge for edge in self.filtered.edges
            if edge.type == "sequence"
            and edge.source in node_ids and edge.target in node_ids
        ]

        self.assertEqual(continue_node.targetStructureGroupId, loop.id)
        self.assertIn(loop.id, continue_node.loopIds)
        self.assertEqual(
            [(item.groupId, item.armLabel) for item in continue_node.branchArms],
            [(group.id, "if")],
        )
        routes = [edge for edge in edges if edge.source == continue_node.id]
        self.assertEqual(len(routes), 1)
        self.assertEqual(routes[0].target, loop.exitNodeId)
        self.assertNotEqual(routes[0].target, group.exitNodeId)
        self.assertEqual(
            {(item.groupId, item.armLabel) for item in routes[0].branchRequirements},
            {(group.id, "if")},
        )
        continue_arm = next(arm for arm in group.arms if arm.label == "if")
        self.assertEqual([exit_.kind for exit_ in continue_arm.exits], ["continue"])

    def test_nested_loop_transfers_target_only_the_nearest_loop_exit(self):
        method = self.method_name(
            "nestedLoopTransferShape", "boolean,boolean,boolean,boolean"
        )
        nodes = self.nodes_for(method)
        node_ids = {node.id for node in nodes}
        loops = [loop for loop in self.filtered.loopGroups if loop.method == method]
        self.assertEqual(len(loops), 2)
        transfers = [
            node for node in nodes
            if node.type == "transfer"
            and node.transferKind in {"break", "continue"}
        ]
        self.assertEqual(
            {node.transferKind for node in transfers}, {"break", "continue"}
        )
        target_ids = {node.targetStructureGroupId for node in transfers}
        self.assertEqual(len(target_ids), 1)
        inner = next(loop for loop in loops if loop.id in target_ids)
        outer = next(loop for loop in loops if loop.id != inner.id)
        edges = [
            edge for edge in self.filtered.edges
            if edge.type == "sequence"
            and edge.source in node_ids and edge.target in node_ids
        ]

        for transfer in transfers:
            self.assertIn(inner.id, transfer.loopIds)
            self.assertIn(outer.id, transfer.loopIds)
            routes = [edge for edge in edges if edge.source == transfer.id]
            self.assertEqual(len(routes), 1)
            self.assertEqual(routes[0].target, inner.exitNodeId)
            self.assertNotEqual(routes[0].target, outer.exitNodeId)
            self.assertFalse(any(
                route.target in {
                    group.exitNodeId for group in self.groups_for(method)
                }
                for route in routes
            ))

    def test_try_groups_have_authoritative_entry_dispatch_and_exit_anchors(self):
        for name, parameters, catch_labels in (
            ("tryCatchShape", "boolean", {"catch1", "noCatch"}),
            ("tryMultipleCatchShape", "int", {"catch1", "catch2", "noCatch"}),
            ("tryFinallyNestedStructureShape", "boolean,boolean", {"catch1", "noCatch"}),
            ("tryReturnFinallyShape", "boolean", {"noCatch"}),
            ("tryThrowCatchFinallyShape", "boolean", {"catch1", "noCatch"}),
            ("tryThrowFromCatchFinallyShape", "boolean", {"catch1", "noCatch"}),
            ("tryFinallyOverridesReturnShape", "boolean", {"noCatch"}),
        ):
            with self.subTest(name=name):
                method = self.method_name(name, parameters)
                nodes = {
                    node.id: node for node in self.raw.nodes
                    if node.callerMethod == method or node.calleeFullName == method
                }
                group = next(
                    group for group in self.raw.branchGroups
                    if group.method == method and group.kind == "TRY"
                )
                self.assertEqual({arm.label for arm in group.arms}, catch_labels)
                self.assertEqual(nodes[group.entryNodeId].structureRole, "entry")
                self.assertEqual(nodes[group.exitNodeId].structureRole, "exit")
                decisions = [
                    node for node in nodes.values()
                    if node.structureGroupId == group.id
                    and node.structureRole == "decision"
                ]
                self.assertEqual(len(decisions), 1)

    def test_try_with_nested_if_and_loop_composes_child_exits_before_try_exit(self):
        method = self.method_name(
            "tryFinallyNestedStructureShape", "boolean,boolean"
        )
        nodes = {
            node.id: node for node in self.raw.nodes
            if node.callerMethod == method or node.calleeFullName == method
        }
        edges = {
            (edge.source, edge.target)
            for edge in self.raw.edges
            if edge.type == "sequence"
            and edge.source in nodes and edge.target in nodes
        }
        groups = [
            group for group in self.raw.branchGroups if group.method == method
        ]
        try_group = next(group for group in groups if group.kind == "TRY")
        catch_if = next(
            group for group in groups
            if group.kind == "IF"
            and any(ref.groupId == try_group.id
                    for ref in nodes[group.entryNodeId].branchArms)
        )
        body_if = next(
            group for group in groups
            if group.kind == "IF" and group.id != catch_if.id
        )
        loop = next(loop for loop in self.raw.loopGroups if loop.method == method)
        protected_tail = next(
            node for node in nodes.values()
            if node.calleeFullName == f"{CLASS}.doY:void()"
        )
        finally_call = next(
            node for node in nodes.values()
            if node.calleeFullName == f"{CLASS}.doInner:void()"
            and not node.loopIds
        )
        continuation = next(
            node for node in nodes.values()
            if node.calleeFullName == f"{CLASS}.doHelper:void()"
            and not node.branchArms
        )

        self.assertIn((try_group.entryNodeId, body_if.entryNodeId), edges)
        self.assertIn((loop.exitNodeId, body_if.exitNodeId), edges)
        self.assertIn((body_if.exitNodeId, protected_tail.id), edges)
        try_decision = next(
            node.id for node in nodes.values()
            if node.structureGroupId == try_group.id
            and node.structureRole == "decision"
        )
        self.assertIn((protected_tail.id, try_decision), edges)
        self.assertIn((try_decision, catch_if.entryNodeId), edges)
        self.assertIn((catch_if.exitNodeId, finally_call.id), edges)
        self.assertIn((finally_call.id, try_group.exitNodeId), edges)
        self.assertIn((try_group.exitNodeId, continuation.id), edges)
        self.assertNotIn((catch_if.exitNodeId, try_group.exitNodeId), edges)

    def test_throwing_if_arm_terminates_before_try_catch_alternatives(self):
        method = self.method_name("tryCatchShape", "boolean")
        nodes = {
            node.id: node for node in self.raw.nodes
            if node.callerMethod == method or node.calleeFullName == method
        }
        groups = [
            group for group in self.raw.branchGroups if group.method == method
        ]
        try_group = next(group for group in groups if group.kind == "TRY")
        inner_if = next(group for group in groups if group.kind == "IF")
        try_decision = next(
            node.id for node in nodes.values()
            if node.structureGroupId == try_group.id
            and node.structureRole == "decision"
        )
        throw_exit = next(
            node for node in nodes.values()
            if node.exitKind == "throw"
            and any(ref.groupId == inner_if.id for ref in node.branchArms)
        )
        throw_operator = next(
            node for node in nodes.values()
            if node.calleeFullName == "<operator>.throw"
            and any(ref.groupId == inner_if.id for ref in node.branchArms)
        )
        protected_tail = next(
            node for node in nodes.values()
            if node.calleeFullName == f"{CLASS}.doHelper:void()"
        )
        edges = {
            (edge.source, edge.target)
            for edge in self.raw.edges
            if edge.type == "sequence"
        }

        self.assertIn((throw_operator.id, throw_exit.id), edges)
        self.assertNotIn((throw_operator.id, try_decision), edges)
        self.assertIn((protected_tail.id, try_decision), edges)

    def test_branch_then_try_keeps_the_try_body_entry_route(self):
        method = self.method_name("branchThenTryInlineEntryShape", "boolean")
        nodes = {
            node.id: node for node in self.raw.nodes
            if node.callerMethod == method or node.calleeFullName == method
        }
        groups = [
            group for group in self.raw.branchGroups if group.method == method
        ]
        preceding_if = next(group for group in groups if group.kind == "IF")
        try_group = next(group for group in groups if group.kind == "TRY")
        first_body_call = next(
            node.id for node in nodes.values()
            if node.calleeFullName == f"{CLASS}.helperA:int()"
        )
        edges = {
            (edge.source, edge.target)
            for edge in self.raw.edges
            if edge.type == "sequence"
        }

        self.assertIn((preceding_if.exitNodeId, try_group.entryNodeId), edges)
        self.assertIn((try_group.entryNodeId, first_body_call), edges)

    def test_break_targeting_a_loop_inside_try_reaches_finally_after_loop_exit(self):
        method = self.method_name("tryLoopBreakFinallyShape", "boolean,boolean")
        nodes = {
            node.id: node for node in self.raw.nodes
            if node.callerMethod == method or node.calleeFullName == method
        }
        loop = next(group for group in self.raw.loopGroups if group.method == method)
        try_group = next(
            group for group in self.raw.branchGroups
            if group.method == method and group.kind == "TRY"
        )
        break_node = next(
            node for node in nodes.values()
            if node.type == "transfer" and node.transferKind == "break"
        )
        try_decision = next(
            node.id for node in nodes.values()
            if node.structureGroupId == try_group.id
            and node.structureRole == "decision"
        )
        edges = {
            (edge.source, edge.target)
            for edge in self.raw.edges if edge.type == "sequence"
        }

        self.assertIn((break_node.id, loop.exitNodeId), edges)
        self.assertIn((loop.exitNodeId, try_decision), edges)

    def test_return_inside_try_executes_finally_before_terminal_exit(self):
        method = self.method_name("tryReturnFinallyShape", "boolean")
        nodes = {
            node.id: node for node in self.raw.nodes
            if node.callerMethod == method or node.calleeFullName == method
        }
        edges = [
            edge for edge in self.raw.edges
            if edge.type == "sequence"
            and edge.source in nodes and edge.target in nodes
        ]
        try_group = next(
            group for group in self.raw.branchGroups
            if group.method == method and group.kind == "TRY"
        )
        inner_if = next(
            group for group in self.raw.branchGroups
            if group.method == method and group.kind == "IF"
        )
        decision = inner_if.conditionStages[0].decisionNodeId
        return_node = next(node for node in nodes.values() if node.exitKind == "return")
        finally_call = next(
            node for node in nodes.values()
            if node.calleeFullName == f"{CLASS}.doX:void()"
        )

        self.assertTrue(any(
            edge.source == decision and edge.target == finally_call.id
            for edge in edges
        ))
        self.assertTrue(any(
            edge.source == finally_call.id and edge.target == return_node.id
            for edge in edges
        ))
        self.assertFalse(any(edge.target == return_node.id and edge.source != finally_call.id
                             for edge in edges))
        self.assertTrue(any(
            edge.source == finally_call.id and edge.target == try_group.exitNodeId
            for edge in edges
        ))

    def test_try_normalization_removes_joern_finally_self_cycles(self):
        methods = {
            self.method_name("tryFinallyNestedStructureShape", "boolean,boolean"),
            self.method_name("tryReturnFinallyShape", "boolean"),
            self.method_name("tryThrowCatchFinallyShape", "boolean"),
            self.method_name("tryThrowFromCatchFinallyShape", "boolean"),
            self.method_name("tryFinallyOverridesReturnShape", "boolean"),
        }
        node_methods = {
            node.id: node.callerMethod or node.calleeFullName
            for node in self.raw.nodes
        }
        self.assertFalse(any(
            edge.type == "sequence"
            and edge.source == edge.target
            and node_methods.get(edge.source) in methods
            for edge in self.raw.edges
        ))

    def test_throw_in_catch_runs_finally_before_throw_exit(self):
        method = self.method_name("tryThrowFromCatchFinallyShape", "boolean")
        nodes = {
            node.id: node for node in self.raw.nodes
            if node.callerMethod == method or node.calleeFullName == method
        }
        edges = {
            (edge.source, edge.target)
            for edge in self.raw.edges
            if edge.type == "sequence"
            and edge.source in nodes and edge.target in nodes
        }
        catch_throw = next(
            node for node in nodes.values()
            if node.exitKind == "throw" and node.branchArms
            and any(ref.armLabel == "catch1" for ref in node.branchArms)
        )
        finally_call = next(
            node for node in nodes.values()
            if node.calleeFullName == f"{CLASS}.doX:void()"
        )
        self.assertIn((finally_call.id, catch_throw.id), edges)
        self.assertFalse(any(
            target == catch_throw.id and source != finally_call.id
            for source, target in edges
        ))

    def test_terminal_finally_path_overrides_pending_return(self):
        method = self.method_name("tryFinallyOverridesReturnShape", "boolean")
        nodes = {
            node.id: node for node in self.raw.nodes
            if node.callerMethod == method or node.calleeFullName == method
        }
        edges = {
            (edge.source, edge.target)
            for edge in self.raw.edges
            if edge.type == "sequence"
            and edge.source in nodes and edge.target in nodes
        }
        groups = [
            group for group in self.raw.branchGroups if group.method == method
        ]
        try_group = next(group for group in groups if group.kind == "TRY")
        finally_if = max(
            (group for group in groups if group.kind == "IF"),
            key=lambda group: group.line or 0,
        )
        pending_return = next(
            node for node in nodes.values() if node.exitKind == "return"
        )
        overriding_throw = next(
            node for node in nodes.values()
            if node.exitKind == "throw"
            and any(ref.groupId == finally_if.id for ref in node.branchArms)
        )
        finally_work = next(
            node for node in nodes.values()
            if node.calleeFullName == f"{CLASS}.doX:void()"
        )

        outgoing = {}
        for source, target in edges:
            outgoing.setdefault(source, []).append(target)
        pending = [finally_if.conditionStages[0].decisionNodeId]
        visited = set()
        while pending:
            current = pending.pop()
            if current not in visited:
                visited.add(current)
                pending.extend(outgoing.get(current, []))
        self.assertIn(overriding_throw.id, visited)
        self.assertNotIn((overriding_throw.id, try_group.exitNodeId), edges)
        self.assertIn((finally_work.id, pending_return.id), edges)

    def test_all_sequence_requirements_reference_valid_arms_and_select_targets(self):
        nodes = {node.id: node for node in self.raw.nodes}
        groups = {group.id: group for group in self.raw.branchGroups}
        valid_arms = {
            group.id: {arm.label for arm in group.arms}
            for group in self.raw.branchGroups
        }

        for edge in self.raw.edges:
            if edge.type != "sequence":
                continue
            with self.subTest(edge=f"{edge.source}->{edge.target}"):
                requirements = [
                    (requirement.groupId, requirement.armLabel)
                    for requirement in edge.branchRequirements
                ]
                by_group = {}
                for group_id, arm_label in requirements:
                    self.assertIn(group_id, groups)
                    self.assertIn(arm_label, valid_arms[group_id])
                    self.assertNotIn(group_id, by_group)
                    by_group[group_id] = arm_label

                target_membership = {
                    (membership.groupId, membership.armLabel)
                    for membership in nodes[edge.target].branchArms
                }
                self.assertLessEqual(target_membership, set(requirements))

        for group in self.raw.branchGroups:
            entry_membership = {
                (item.groupId, item.armLabel)
                for item in nodes[group.entryNodeId].branchArms
            }
            if group.exitNodeId is not None:
                self.assertEqual(
                    entry_membership,
                    {
                        (item.groupId, item.armLabel)
                        for item in nodes[group.exitNodeId].branchArms
                    },
                )
                self.assertFalse(any(
                    edge.type == "sequence"
                    and edge.source == group.exitNodeId
                    and any(req.groupId == group.id
                            for req in edge.branchRequirements)
                    for edge in self.raw.edges
                ))

    def test_three_arm_join_keeps_or_paths_separate_before_common_exit(self):
        method = self.method_name("threeArmConvergingShape", "boolean,boolean")
        nodes = {
            node.id: node for node in self.raw.nodes
            if node.callerMethod == method or node.calleeFullName == method
        }
        group = next(
            group for group in self.raw.branchGroups if group.method == method
        )
        edges = [
            edge for edge in self.raw.edges
            if edge.type == "sequence"
            and edge.source in nodes and edge.target in nodes
        ]
        after = next(
            node for node in nodes.values()
            if node.calleeFullName == f"{CLASS}.doY:void()"
        )
        incoming = [edge for edge in edges if edge.target == group.exitNodeId]

        self.assertEqual({arm.label for arm in group.arms}, {"if", "elseif1", "else"})
        self.assertEqual(
            {
                next(req.armLabel for req in edge.branchRequirements
                     if req.groupId == group.id)
                for edge in incoming
            },
            {"if", "elseif1", "else"},
        )
        common = [
            edge for edge in edges
            if edge.source == group.exitNodeId and edge.target == after.id
        ]
        self.assertEqual(len(common), 1)
        self.assertFalse(any(
            req.groupId == group.id for req in common[0].branchRequirements
        ))

    def test_three_arm_terminal_alternative_does_not_pollute_common_exit(self):
        method = self.method_name("threeArmWithReturnShape", "boolean,boolean")
        nodes = {
            node.id: node for node in self.raw.nodes
            if node.callerMethod == method or node.calleeFullName == method
        }
        group = next(
            group for group in self.raw.branchGroups if group.method == method
        )
        edges = [
            edge for edge in self.raw.edges
            if edge.type == "sequence"
            and edge.source in nodes and edge.target in nodes
        ]
        return_node = next(node for node in nodes.values() if node.exitKind == "return")
        after = next(
            node for node in nodes.values()
            if node.calleeFullName == f"{CLASS}.doHelper:void()"
        )
        incoming = [edge for edge in edges if edge.target == group.exitNodeId]

        self.assertEqual(
            {
                next(req.armLabel for req in edge.branchRequirements
                     if req.groupId == group.id)
                for edge in incoming
            },
            {"if", "elseif1"},
        )
        self.assertFalse(any(edge.source == return_node.id for edge in edges))
        self.assertTrue(any(
            edge.target == return_node.id
            and {(req.groupId, req.armLabel) for req in edge.branchRequirements}
                >= {(group.id, "else")}
            for edge in edges
        ))
        common = [
            edge for edge in edges
            if edge.source == group.exitNodeId and edge.target == after.id
        ]
        self.assertEqual(len(common), 1)
        self.assertFalse(any(
            req.groupId == group.id for req in common[0].branchRequirements
        ))

    def test_all_terminal_branch_has_no_normal_continuation(self):
        group = next(
            group for group in self.filtered.branchGroups
            if group.method and ".allTerminalBranchShape:" in group.method
        )
        nodes = {
            node.id: node for node in self.filtered.nodes
            if node.callerMethod == group.method or node.calleeFullName == group.method
        }
        edges = [
            edge for edge in self.filtered.edges
            if edge.type == "sequence"
            and edge.source in nodes and edge.target in nodes
        ]

        self.assertEqual(
            {arm.label: {exit_.kind for exit_ in arm.exits} for arm in group.arms},
            {"if": {"return"}, "else": {"throw"}},
        )
        if group.exitNodeId is not None:
            self.assertFalse(any(edge.target == group.exitNodeId for edge in edges))
            self.assertFalse(any(edge.source == group.exitNodeId for edge in edges))
        terminal_ids = {
            node.id for node in nodes.values()
            if node.type == "exit" and node.exitKind in {"return", "throw"}
        }
        self.assertEqual(len(terminal_ids), 2)
        self.assertFalse(any(edge.source in terminal_ids for edge in edges))

    def test_filtered_empty_structures_are_removed_to_a_fixed_point(self):
        empty_loop_method = self.method_name("filteredEmptyLoopShape", "boolean")
        self.assertFalse(any(
            loop.method == empty_loop_method for loop in self.filtered.loopGroups
        ))
        empty_loop_nodes = self.nodes_for(empty_loop_method)
        self.assertFalse(any(node.loopIds for node in empty_loop_nodes))
        self.assertEqual(
            [
                node.calleeFullName for node in empty_loop_nodes
                if node.type == "call" and node.callerMethod == empty_loop_method
            ],
            [f"{CLASS}.doX:void()"],
        )

        empty_branch_method = self.method_name(
            "nestedConsecutiveEmptyGroupsShape", "boolean,boolean,boolean"
        )
        self.assertEqual(self.groups_for(empty_branch_method), [])
        empty_branch_nodes = self.nodes_for(empty_branch_method)
        self.assertFalse(any(node.branchArms for node in empty_branch_nodes))
        self.assertFalse(any(
            requirement.groupId.startswith("cs")
            for edge in self.filtered.edges
            if edge.source in {node.id for node in empty_branch_nodes}
            for requirement in edge.branchRequirements
        ))

    def test_switch_local_break_is_not_mistaken_for_loop_break(self):
        method = self.method_name("loopWithSwitchBreakShape", "boolean,int")
        nodes = self.nodes_for(method)
        node_ids = {node.id for node in nodes}
        loop = next(loop for loop in self.filtered.loopGroups if loop.method == method)
        calls = {
            node.code: node.id for node in nodes
            if node.type == "call"
        }
        edges = {
            (edge.source, edge.target)
            for edge in self.filtered.edges
            if edge.type == "sequence"
            and edge.source in node_ids and edge.target in node_ids
        }

        self.assertFalse(any(
            node.type == "transfer" and node.transferKind == "break"
            for node in nodes
        ))
        self.assertIn((calls["this.doInner()"], calls["this.doY()"]), edges)
        self.assertIn((calls["this.doHelper()"], calls["this.doY()"]), edges)
        self.assertNotIn((calls["this.doInner()"], loop.exitNodeId), edges)
        self.assertIn((calls["this.doY()"], loop.exitNodeId), edges)

    def test_every_normalized_method_is_acyclic_and_anchor_scopes_match(self):
        methods = build_method_definitions(self.filtered)
        for method in methods.values():
            with self.subTest(method=method.methodFullName):
                nodes = {method.entry.id: method.entry, **{
                    node.id: node for node in method.nodes
                }}
                outgoing: dict[str, list[str]] = {}
                for edge in method.sequenceEdges:
                    outgoing.setdefault(edge.source, []).append(edge.target)

                active: set[str] = set()
                complete: set[str] = set()

                def visit(node_id: str) -> None:
                    if node_id in complete:
                        return
                    self.assertNotIn(
                        node_id, active,
                        f"cycle re-enters {node_id} in {method.methodFullName}",
                    )
                    active.add(node_id)
                    for target_id in outgoing.get(node_id, []):
                        visit(target_id)
                    active.remove(node_id)
                    complete.add(node_id)

                visit(method.entry.id)
                for node_id in nodes:
                    visit(node_id)

                for group in method.branchGroups:
                    entry = nodes[group.entryNodeId]
                    self.assertNotIn(group.id, {
                        ref.groupId for ref in entry.branchArms
                    })
                    if group.exitNodeId is not None:
                        exit_node = nodes[group.exitNodeId]
                        self.assertEqual(entry.branchArms, exit_node.branchArms)
                        self.assertNotIn(group.id, {
                            ref.groupId for ref in exit_node.branchArms
                        })
                    self.assertTrue(all(arm.exits for arm in group.arms))

                for loop in method.loopGroups:
                    entry = nodes[loop.entryNodeId]
                    exit_node = nodes[loop.exitNodeId]
                    self.assertEqual(entry.loopIds, exit_node.loopIds)
                    self.assertNotIn(loop.id, entry.loopIds)
                    self.assertNotIn(loop.id, exit_node.loopIds)

                for node in nodes.values():
                    if node.type != "transfer":
                        continue
                    target_loop = next(
                        loop for loop in method.loopGroups
                        if loop.id == node.targetStructureGroupId
                    )
                    self.assertEqual(
                        outgoing.get(node.id, []), [target_loop.exitNodeId]
                    )

if __name__ == "__main__":
    unittest.main()
