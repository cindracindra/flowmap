from pathlib import Path


SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "backend"
    / "src"
    / "flowmap"
    / "joern"
    / "scripts"
    / "full_cfg.sc"
)


def test_method_entry_ignores_parallel_synthetic_method_return() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert "val bodyEntryStartPoints = rawEntryStartPoints.filterNot" in source
    assert "methodReturn.exists(_.id == node.id)" in source
    assert "if (bodyEntryStartPoints.nonEmpty) bodyEntryStartPoints" in source
    assert "candidateProjectedIds(entryStartPoints.map(_.id))" in source
    assert "directExitIds(method.start.cfgNext.l)" not in source


def test_no_call_decisions_use_explicit_anchors_without_predecessor_inference() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert "def deriveLexicalOwnership(" in source
    assert "writeLexicalOwnership(" in source
    assert "structuralOwnership.getOrElse(stage.decisionNodeId" in source
    assert "def precedingConditionCalls(structure: ControlStructure)" not in source
    assert "previousAstDecisionId" not in source
    assert "ifDecisionSources" not in source
    assert "branchEntryEdges" not in source


def test_enhanced_for_back_edge_is_classified_from_lexical_body_boundary() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert "val isSourceForEach" in source
    assert 'case "FOR_EACH" =>' in source
    assert "plan.bodyNodeIds.contains(source)" in source
    assert "restartsAtGuard || restartsAtEntry" in source


def test_structure_discovery_is_separate_from_legacy_route_mutation() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    for model in (
        "case class StructureRegion(",
        "case class ArmRegion(",
        "case class ConditionStageRegion(",
        "case class BranchRegion(",
        "case class LoopRegion(",
    ):
        assert model in source

    branch_emission = source.index("emitIfChain(")
    loop_emission = source.index("emitLoopGroup(")
    assert branch_emission < source.index("discoveredBranchRegions ++= methodBranchRegions")
    assert loop_emission < source.index("discoveredLoopRegions ++= methodLoopRegions")
    assert "discoveredBranchRegions ++= methodBranchRegions" in source
    assert "discoveredLoopRegions ++= methodLoopRegions" in source

    # Explicit anchors have removed predecessor-based compatibility payloads.
    assert "case class LoopAnchorPlan(" in source
    assert "val pendingDecisions = mutable.LinkedHashMap" not in source
    assert "ifDecisionSources" not in source
    assert "branchEntryEdges" not in source


def test_lexical_ownership_is_assigned_once_from_discovered_regions() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert "case class LexicalOwnership(" in source
    assert "def deriveLexicalOwnership(" in source
    assert "region.arms.foreach" in source
    assert "region.bodyNodeIds ++ region.guardNodeIds ++ region.updateNodeIds" in source
    assert "structuralOwnership(structure.entryAnchorId) = enclosure" in source
    assert "structuralOwnership(structure.exitAnchorId) = enclosure" in source
    assert "writeLexicalOwnership(" in source
    assert "structuralOwnership.getOrElse(stage.decisionNodeId" in source
    assert "writeLexicalOwnership(" in source
    assert "targetStructureGroupId" in source

    # Emitter-local membership mutation has been removed.
    assert "val nestedStructures = armRoot.ast.collectAll[ControlStructure].l" not in source
    assert "armTags.getOrElseUpdate(structure.id" not in source
    assert "loopTags.getOrElseUpdate(c.id" not in source


def test_candidate_cfg_keeps_cycle_edges_and_path_local_traversal() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert "case class CandidateCfgEdge(" in source
    assert "cycleClosing: Boolean" in source
    assert "case class CandidateMethodCfg(" in source
    assert "def discoverCandidateCfg(" in source
    assert "val globallyExpanded = mutable.Set[Long]()" in source
    assert "def visit(node: CfgNode, activePath: Set[Long])" in source
    assert "val closesActivePath = activePath.contains(successor.id)" in source
    assert "record(node.id, successor.id, closesActivePath)" in source
    assert "if (!closesActivePath && !globallyExpanded.contains(successor.id))" in source
    assert "visit(successor, activePath + successor.id)" in source
    assert "validateStraightLineCandidateParity(" not in source

    # Candidate discovery is the sole owner of projected ordering.
    assert "discoveredCandidateCfgs += candidateCfg" in source
    assert "legacyNextCallsById" not in source


def test_if_selection_uses_explicit_structure_anchors_and_condition_stages() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert "case class IfAnchorPlan(" in source
    assert '"structureRole" -> "entry"' in source
    assert '"structureRole" -> "decision"' in source
    assert '"structureRole" -> "exit"' in source
    assert '"entryNodeId" -> region.structure.entryAnchorId' in source
    assert '"conditionStages" -> ujson.Arr(serializedStages*)' in source
    assert "pendingIfAnchorPlans.sortBy(plan => -plan.astSize)" in source
    assert "selectedArm.entryIds ++" in source
    assert "if (selectedArm.entryIds.isEmpty) selectedArm.terminalIds else Nil" in source
    assert "plan.exitNodeId.toList" in source
    assert "plan.continuationIds.foreach" in source
    assert "def conditionExecutionCallsOf" in source
    assert "stage.conditionExecutionIds.sliding(2)" in source
    assert "isInternalConditionEdge" in source


def test_structure_continuations_are_cfg_first_and_ast_fallback_targets_anchors() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert "def projectedCfgBoundaryContinuationIds(" in source
    assert "nearestProjectedIds(boundaryStarts)" in source
    # Definition plus IF, TRY, and loop callers.
    assert source.count("projectedCfgBoundaryContinuationIds(") >= 4
    assert "def exceptionalAstStructuralContinuationIds(" in source
    assert 'List(s"cs${structure.id}:entry")' in source
    assert 'List(s"loop${structure.id}:entry")' in source
    assert 'List(s"b${structure.id}")' not in source
    assert "def nextAstContinuationIds(" not in source


def test_fallthrough_nodes_are_materialized_after_structure_normalization() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    materialization = source.index(
        "A structure exit can be the first route to expose method fallthrough"
    )
    composition = source.index("pendingIfAnchorPlans.sortBy(_.astSize)")
    validation = source.index("val finalSequenceEdges = edges.collect")

    assert "case class FallthroughPlan(" in source
    assert "pendingFallthroughPlans += FallthroughPlan(" in source
    assert composition < materialization < validation
    assert "val danglingSequenceEndpoints = edges.collect" in source
    assert "Normalized method graph has dangling sequence endpoints" in source


def test_try_body_throws_remain_terminal_instead_of_entering_catch_dispatch() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert "val pendingTerminalIds = (terminalIds -- finallyIds)" in source
    assert "bodyThrowIds.contains(terminalId) && plan.catchArms.nonEmpty" not in source
    assert "addTrySequence(source, terminalId, nodeBranchMembership(terminalId))" in source


def test_branch_convergence_is_normalized_child_first_through_exit() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert "pendingIfAnchorPlans.sortBy(_.astSize)" in source
    assert "val boundaryCandidates = edges.collect" in source
    assert "val normalOutgoingBySource = edges.collect" in source
    assert "normalOutgoingBySource.getOrElse(source, Nil).forall" in source
    assert "val interArmKeys = edges.collect" in source
    assert "!isTerminalTransfer(edge.obj(\"from\").str)" in source
    assert "addIfSequence(frontierId, exitId)" in source
    assert "def canonicalContinuation(targetId: String)" in source
    assert "ifPlanByGroupId.get(parentGroupId).flatMap(_.exitNodeId)" in source
    assert "val targetsInsideEnclosingBranch" in source
    assert "val preferredPhysicalTargets" in source
    assert "addIfSequence(exitId, targetId)" in source
    assert 'exitValue.obj("destinationNodeId") = ujson.Str(exitId)' in source


def test_break_and_continue_are_single_target_structural_transfers() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert "case class StructuralTransferPlan" in source
    assert '"type" -> "transfer", "transferKind" -> "break"' in source
    assert '"type" -> "transfer", "transferKind" -> "continue"' in source
    assert 's"n${continueNode.id}"' in source
    assert "pendingStructuralTransfers += StructuralTransferPlan" in source
    assert "val transferTargetIds = pendingStructuralTransfers.map(_.nodeId).toSet" in source
    assert "normalizedLoopExitByGroupId.get(transfer.targetGroupId)" in source
    assert "addIfSequence(transfer.nodeId, exitId)" in source


def test_loop_anchors_wrap_the_single_iteration_route() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert '"entryNodeId" -> region.structure.entryAnchorId' in source
    assert '"exitNodeId" -> region.structure.exitAnchorId' in source
    assert "pendingLoopAnchorPlans.sortBy(_.astSize)" in source
    assert "val discoveredInitialIds = edges.collect" in source
    assert "addIfSequence(source, plan.entryNodeId)" in source
    assert "addIfSequence(plan.entryNodeId, target)" in source
    assert "addIfSequence(plan.exitNodeId, target)" in source
    assert "plan.enclosingLoopIds.lastOption" in source
    assert "loopExitByGroupId.get(loopGroupId)" in source


def test_loop_normalization_is_centralized_and_legacy_repairs_are_removed() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert "def isRepetitionEdge(source: String, target: String)" in source
    assert 'case "DO" | "DO_WHILE" =>' in source
    assert 'case "FOR" if plan.updateNodeIds.nonEmpty =>' in source
    assert 'case "FOR_EACH" =>' in source
    assert "val repetitionEdges = edges.collect" in source
    assert "Normalized method graph is cyclic" in source
    assert "candidateCycleClosingKeys" in source
    for legacy in (
        "case class StaticLoopRoutes(",
        "iterationReplacements",
        "rejectedDecisionPredecessors",
        "bypassCallPairs",
        "bypassExitPairs",
        "pendingLoopAnchorRoutes",
    ):
        assert legacy not in source


def test_structure_composition_precedes_final_method_acyclic_validation() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    loop_normalization = source.index("pendingLoopAnchorPlans.sortBy(_.astSize)")
    transfer_routing = source.index("pendingStructuralTransfers.foreach")
    branch_composition = source.index("pendingIfAnchorPlans.sortBy(_.astSize)")
    final_validation = source.index("val finalSequenceEdges = edges.collect")
    assert loop_normalization < transfer_routing < branch_composition < final_validation
    assert "Structural entry $entryId must have an outgoing route" in source
    assert "Reached structural exit $exitId must have an outgoing route" in source
    assert "Normalized method graph is cyclic" in source


def test_requirements_are_derived_once_after_topology_normalization() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    requirement_pass = source.index(
        "// Package 12: derive serialized requirements exactly once"
    )
    acyclic_validation = source.index("val active = mutable.Set[String]()")
    assert acyclic_validation < requirement_pass
    assert "val structuralRouteRequirements" in source
    assert "val sourceRequirements = nodeBranchMembership(source)" in source
    assert "val enteredRequirements = nodeBranchMembership(target)" in source
    assert "val expandedRequirementEdges" in source
    assert 'copy("_structuralRouteRequirements")' in source
    assert "_impossibleRequirementRoute" in source
    assert "commonRouteGroups" not in source
    assert "references missing branch group" in source
    assert "does not select target enclosure" in source
    assert "entry/exit enclosure differs" in source
    assert "retains exited-group requirement" in source

    for obsolete in (
        "pendingDecisions",
        "ifDecisionSources",
        "branchEntryEdges",
        "sequenceTargetsBySource",
    ):
        assert obsolete not in source


def test_arm_exits_are_outcomes_not_route_geometry() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    flowmap = SCRIPT.parents[2]
    filtering = (flowmap / "domain" / "cfg_filtering.py").read_text(encoding="utf-8")
    validation = (flowmap / "domain" / "method_structure_validation.py").read_text(
        encoding="utf-8"
    )

    assert '"destinationNodeId" -> ujson.Str' in source
    assert 'exitValue.obj("destinationNodeId")' in source
    assert '"frontierIds"' not in source
    assert '"targetIds"' not in source
    assert "_resolve_arm_exit_geometry" not in filtering
    assert "_nearest_surviving_frontiers" not in filtering
    assert "_nearest_surviving_targets" not in filtering
    assert not (flowmap / "domain" / "method_branch_routing.py").exists()
    assert "normalize_method_arm_exit_targets" not in validation
    assert "materialize_method_empty_arm_routes" not in validation
    assert "recompute_method_branch_geometry" not in validation
    assert "_annotate_filtered_method_routes" not in filtering
    assert "prepare_all_method_branch_routes" not in filtering


def test_candidate_cfg_is_authoritative_for_projected_call_order() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert "legacyNextCallsById" not in source
    assert "candidateProjectedIds(candidateOutgoing.getOrElse(call.id, Nil))" in source
    assert "LinkedHashSet[String]" in source
    assert "canonicalProjectedSuccessors(call)" in source
    assert "nestedCallIdsByCallId" in source
    assert "node.start.astChildren.l.sortBy(_.order).flatMap(postOrder)" in source
    assert "conditionExitCallsOf" not in source
    assert "if (physicalContinuationIds.nonEmpty) physicalContinuationIds" in source


def test_normalized_output_has_no_legacy_branch_or_loopback_contract() -> None:
    source = SCRIPT.read_text(encoding="utf-8")

    assert '"branchGroupId"' not in source
    assert '"loopBack"' not in source


def test_switch_local_break_is_not_emitted_as_a_loop_transfer() -> None:
    source = SCRIPT.read_text()

    assert "def breakTargetsLoop(breakNode: ControlStructure): Boolean" in source
    assert 'else if (kind == "SWITCH")' in source
    assert 'node.controlStructureType == "BREAK" && breakTargetsLoop(node)' in source
    assert '"returnFrom"' not in source
    assert '"fallback"' not in source
