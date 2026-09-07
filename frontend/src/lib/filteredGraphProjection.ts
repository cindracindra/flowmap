import type {
  BranchRequirement,
  FlowEdge,
  FlowNode,
} from "../types/flowmap";
import type { GraphBundle, MethodDefinition } from "../types/filteredGraph";
import { sequenceOrderedNodes } from "./sequenceTraversal";

export type InstanceNodeId = string;
export type BranchInstanceId = string;
export type CallInstanceId = string;

export interface VisiblePhase {
  id: string;
  definitionPhaseId: string;
  label?: string;
  index: number;
}

export interface VisibleNode {
  id: InstanceNodeId;
  instanceId: string;
  definitionNodeId: string;
  methodEntryId: string;
  depth: number;
  node: FlowNode;
  phase?: VisiblePhase;
  retainedCall: boolean;
  expandable: boolean;
  expanded: boolean;
  recursiveCutoff: boolean;
  retainedCalleePhaseCount?: number;
  branchRequirements: VisibleBranchRequirement[];
}

export interface VisibleEdge extends FlowEdge {
  kind: "sequence" | "invoke";
}

export interface VisibleBranchRequirement {
  groupId: BranchInstanceId;
  armLabel: string;
}

export interface VisibleArmExit {
  kind: "return" | "throw" | "break" | "continue" | "continues";
  destinationNodeId?: InstanceNodeId;
}

export interface VisibleConditionStage {
  id: string;
  nodeIds: InstanceNodeId[];
  decisionNodeId?: InstanceNodeId;
  /** Canonical condition text retained after its graph node becomes an alias. */
  code?: string;
}

export interface VisibleArmConditionStage {
  stageId: string;
  nodeIds: InstanceNodeId[];
  outcome: boolean;
}

export interface VisibleBranchArm {
  label: string;
  empty: boolean;
  exits: VisibleArmExit[];
  conditionCode?: string;
  conditionStages?: VisibleArmConditionStage[];
  exceptionType?: string;
}

export interface VisibleBranchGroup {
  id: BranchInstanceId;
  instanceId: string;
  definitionBranchId: string;
  kind: string;
  method?: string;
  line?: number;
  entryNodeId?: InstanceNodeId;
  exitNodeId?: InstanceNodeId;
  conditionStages?: VisibleConditionStage[];
  arms: VisibleBranchArm[];
  selectedArmLabel: string;
  enclosingRequirements?: VisibleBranchRequirement[];
  /** Visible topology immediately around the hidden structural anchors. */
  entryPredecessorIds: InstanceNodeId[];
  entrySuccessorIds: InstanceNodeId[];
  /** End of a TRY body, immediately before catch/no-catch selection. */
  decisionPredecessorIds: InstanceNodeId[];
  exitPredecessorIds: InstanceNodeId[];
  continuationIds: InstanceNodeId[];
  /** Call-site anchor used only by synthetic dispatch selectors. */
  dispatchAnchorId?: InstanceNodeId;
}

export interface VisibleMethodExit {
  instanceId: string;
  sourceNodeId: InstanceNodeId;
  kind: "return" | "fallthrough" | "throw";
  branchRequirements?: VisibleBranchRequirement[];
}

export interface VisibleGraphProjection {
  rootId: InstanceNodeId;
  nodes: VisibleNode[];
  edges: VisibleEdge[];
  branchGroups: VisibleBranchGroup[];
  exits: VisibleMethodExit[];
}

export function instanceNodeId(instanceId: string, definitionNodeId: string): InstanceNodeId {
  return `${instanceId}:${definitionNodeId}`;
}

export function callInstanceId(instanceId: string, callNodeId: string): CallInstanceId {
  return `${instanceId}/call:${callNodeId}`;
}

export function branchInstanceId(
  instanceId: string,
  definitionBranchId: string,
): BranchInstanceId {
  return `${instanceId}/branch:${definitionBranchId}`;
}

function instantiateRequirements(
  instanceId: string,
  requirements: readonly BranchRequirement[] | undefined,
): VisibleBranchRequirement[] {
  return (requirements ?? []).map((requirement) => ({
    groupId: branchInstanceId(instanceId, requirement.groupId),
    armLabel: requirement.armLabel,
  }));
}

function instantiateBranchGroups(
  method: MethodDefinition,
  instanceId: string,
  selectedBranchArms: ReadonlyMap<BranchInstanceId, string>,
): VisibleBranchGroup[] {
  const rewriteNodeIds = (ids: readonly string[] | undefined): InstanceNodeId[] | undefined =>
    ids?.map((nodeId) => instanceNodeId(instanceId, nodeId));

  return method.branchGroups.map((group) => {
    const id = branchInstanceId(instanceId, group.id);
    const requestedArm = selectedBranchArms.get(id);
    const preferredArm = group.arms.find((arm) => arm.label === "if")
      ?? group.arms.find((arm) => arm.label === "try")
      ?? group.arms.find((arm) => arm.label === "noCatch")
      ?? group.arms[0];
    const selectedArm = group.arms.find((arm) => arm.label === requestedArm)
      ?? preferredArm;

    return {
      id,
      instanceId,
      definitionBranchId: group.id,
      kind: group.kind,
      method: group.method,
      line: group.line,
      arms: group.arms.map((arm) => ({
        label: arm.label,
        empty: arm.empty,
        exits: arm.exits.map((exit) => ({
          kind: exit.kind,
          destinationNodeId: exit.destinationNodeId
            ? instanceNodeId(instanceId, exit.destinationNodeId)
            : undefined,
        })),
        conditionCode: arm.conditionCode,
        conditionStages: arm.conditionStages?.map((stage) => ({
          stageId: stage.stageId,
          nodeIds: rewriteNodeIds(stage.nodeIds) ?? [],
          outcome: stage.outcome,
        })),
        exceptionType: arm.exceptionType,
      })),
      selectedArmLabel: selectedArm?.label ?? "",
      entryNodeId: group.entryNodeId
        ? instanceNodeId(instanceId, group.entryNodeId)
        : undefined,
      exitNodeId: group.exitNodeId
        ? instanceNodeId(instanceId, group.exitNodeId)
        : undefined,
      conditionStages: group.conditionStages?.map((stage) => ({
        id: stage.id,
        nodeIds: rewriteNodeIds(stage.nodeIds) ?? [],
        decisionNodeId: stage.decisionNodeId
          ? instanceNodeId(instanceId, stage.decisionNodeId)
          : undefined,
      })),
      enclosingRequirements: instantiateRequirements(
        instanceId,
        group.enclosingRequirements,
      ),
      entryPredecessorIds: [],
      entrySuccessorIds: [],
      decisionPredecessorIds: [],
      exitPredecessorIds: [],
      continuationIds: [],
    };
  });
}

function requirementsMatch(
  requirements: readonly VisibleBranchRequirement[] | undefined,
  selectedArms: ReadonlyMap<BranchInstanceId, string>,
): boolean {
  return (requirements ?? []).every(
    (requirement) => selectedArms.get(requirement.groupId) === requirement.armLabel,
  );
}

function mergedBranchRequirements(
  incoming: readonly VisibleBranchRequirement[] | undefined,
  outgoing: readonly VisibleBranchRequirement[] | undefined,
): VisibleBranchRequirement[] | null {
  const byGroup = new Map<string, string>();
  for (const requirement of [...(incoming ?? []), ...(outgoing ?? [])]) {
    const existing = byGroup.get(requirement.groupId);
    if (existing !== undefined && existing !== requirement.armLabel) return null;
    byGroup.set(requirement.groupId, requirement.armLabel);
  }
  return [...byGroup].map(([groupId, armLabel]) => ({ groupId, armLabel }));
}

/** Bridge invisible control-structure nodes without changing route semantics. */
function bridgeHiddenNodes(
  nodes: readonly VisibleNode[],
  sourceEdges: readonly VisibleEdge[],
  hiddenNodeIds: ReadonlySet<string>,
): { nodes: VisibleNode[]; edges: VisibleEdge[] } {
  let edges = [...sourceEdges];
  for (const hiddenNodeId of hiddenNodeIds) {
    const incoming = edges.filter((edge) => edge.to === hiddenNodeId);
    const outgoing = edges.filter((edge) => edge.from === hiddenNodeId);
    edges = edges.filter((edge) =>
      edge.from !== hiddenNodeId && edge.to !== hiddenNodeId);

    for (const before of incoming) {
      for (const after of outgoing) {
        const branchRequirements = mergedBranchRequirements(
          before.branchRequirements,
          after.branchRequirements,
        );
        if (branchRequirements === null || before.from === after.to) continue;
        edges.push({
          ...after,
          from: before.from,
          to: after.to,
          type: "sequence",
          kind: "sequence",
          branchRequirements,
        });
      }
    }
  }

  const uniqueEdges = new Map<string, VisibleEdge>();
  for (const edge of edges) {
    const requirements = (edge.branchRequirements ?? [])
      .map((requirement) => `${requirement.groupId}:${requirement.armLabel}`)
      .sort()
      .join("|");
    uniqueEdges.set(`${edge.from}->${edge.to}:${edge.kind}:${requirements}`, edge);
  }
  return {
    nodes: nodes.filter((node) => !hiddenNodeIds.has(node.id)),
    edges: [...uniqueEdges.values()],
  };
}

function phasesByNode(method: MethodDefinition, instanceId: string): Map<string, VisiblePhase> {
  return new Map(method.phases.flatMap((phase, index) => {
    const visiblePhase = {
      id: `${instanceId}:phase:${phase.id}`,
      definitionPhaseId: phase.id,
      label: phase.label,
      index,
    };
    return phase.memberNodeIds.map((nodeId) => [nodeId, visiblePhase] as const);
  }));
}

/**
 * Build caller-local flow with optionally attached callee bodies.
 *
 * Local sequence edges are immutable presentation facts: expanding a call
 * adds an invoke attachment but never suppresses the caller continuation and
 * never creates an exit-to-caller resume edge. A non-retained callee inherits
 * the phase of its caller call, matching the existing flattened overlay.
 */
export function projectVisibleGraph(
  bundle: GraphBundle,
  operationId: string,
  expandedCallInstanceIds: ReadonlySet<string>,
  selectedBranchArms: ReadonlyMap<BranchInstanceId, string> = new Map(),
  rootMethodEntryId?: string,
  selectedTargetByCallInstanceId: ReadonlyMap<CallInstanceId, string> = new Map(),
): VisibleGraphProjection | null {
  const operation = bundle.operationsById[operationId];
  if (!operation) return null;
  const projectionRootEntryId = rootMethodEntryId ?? operation.rootEntryId;
  if (!bundle.methodsByEntryId[projectionRootEntryId]) return null;

  const nodes: VisibleNode[] = [];
  const edges: VisibleEdge[] = [];
  const branchGroups: VisibleBranchGroup[] = [];
  const exits: VisibleMethodExit[] = [];
  const resolvedBranchArms = new Map<BranchInstanceId, string>();
  const emittedNodes = new Set<string>();

  const transitiveRetainedPhaseKeys = (
    methodEntryId: string,
    instanceId: string,
    activeMethods: ReadonlySet<string>,
  ): Set<string> => {
    const method = bundle.methodsByEntryId[methodEntryId];
    if (!method) return new Set();
    const phaseKeys = new Set(
      method.phases.map((phase) => `${methodEntryId}:${phase.id}`),
    );
    for (const retainedCallNodeId of method.retainedCallNodeIds) {
      const call = method.calls[retainedCallNodeId];
      const targets = call?.targetEntryIds ?? [];
      const retainedCallId = callInstanceId(instanceId, retainedCallNodeId);
      const requestedTarget = selectedTargetByCallInstanceId.get(retainedCallId);
      const selectedTarget = targets.includes(requestedTarget ?? "")
        ? requestedTarget!
        : targets[0];
      if (!selectedTarget || activeMethods.has(selectedTarget)) continue;
      const targetIndex = targets.indexOf(selectedTarget);
      const childInstanceId = `${retainedCallId}/target:${targetIndex}:${selectedTarget}`;
      const childActiveMethods = new Set(activeMethods);
      childActiveMethods.add(selectedTarget);
      for (const phaseKey of transitiveRetainedPhaseKeys(
        selectedTarget,
        childInstanceId,
        childActiveMethods,
      )) phaseKeys.add(phaseKey);
    }
    return phaseKeys;
  };

  const instantiate = (
    methodEntryId: string,
    instanceId: string,
    depth: number,
    activeMethods: ReadonlySet<string>,
    inheritedPhase?: VisiblePhase,
  ): void => {
    const method = bundle.methodsByEntryId[methodEntryId];
    if (!method) return;
    const phaseForNode = phasesByNode(method, instanceId);
    const retainedCalls = new Set(method.retainedCallNodeIds);
    const definitionNodes = sequenceOrderedNodes(
      [method.entry, ...method.nodes],
      method.sequenceEdges,
      method.entryId,
    );

    const instanceBranchGroups = instantiateBranchGroups(
      method,
      instanceId,
      selectedBranchArms,
    );
    branchGroups.push(...instanceBranchGroups);
    for (const group of instanceBranchGroups) {
      resolvedBranchArms.set(group.id, group.selectedArmLabel);
    }
    exits.push(...method.exits.map((exit) => ({
      instanceId,
      sourceNodeId: instanceNodeId(instanceId, exit.sourceNodeId),
      kind: exit.kind,
      branchRequirements: exit.branchRequirements
        ? instantiateRequirements(instanceId, exit.branchRequirements)
        : undefined,
    })));

    for (const node of definitionNodes) {
      const id = instanceNodeId(instanceId, node.id);
      if (emittedNodes.has(id)) continue;
      const branchRequirements = instantiateRequirements(instanceId, node.branchArms);
      emittedNodes.add(id);
      const call = method.calls[node.id];
      const callId = callInstanceId(instanceId, node.id);
      const targets = call?.targetEntryIds ?? [];
      const requestedTarget = selectedTargetByCallInstanceId.get(callId);
      const selectedTarget = targets.includes(requestedTarget ?? "")
        ? requestedTarget!
        : targets[0];
      const recursiveCutoff = targets.length > 0
        && targets.every((target) => activeMethods.has(target));
      const visibleNode: VisibleNode = {
        id,
        instanceId,
        definitionNodeId: node.id,
        methodEntryId,
        depth,
        node,
        // MethodAnalysis excludes structural entry/exit nodes. Inherited
        // callees can take the caller phase immediately; local structural
        // nodes are attached to their visible neighbour after branch filtering.
        // Retained call sites themselves remain deliberately unphased.
        phase: inheritedPhase
          ?? phaseForNode.get(node.id),
        retainedCall: retainedCalls.has(node.id),
        expandable: targets.length > 0,
        expanded: targets.length > 0 && expandedCallInstanceIds.has(callId),
        recursiveCutoff,
        retainedCalleePhaseCount: retainedCalls.has(node.id) && selectedTarget
          ? transitiveRetainedPhaseKeys(
              selectedTarget,
              `${callId}/target:${targets.indexOf(selectedTarget)}:${selectedTarget}`,
              new Set([...activeMethods, selectedTarget]),
            ).size
          : undefined,
        branchRequirements,
      };
      nodes.push(visibleNode);

      if (!visibleNode.expanded) continue;
      if (targets.length > 1 && selectedTarget) {
        const selectedTargetIndex = targets.indexOf(selectedTarget);
        const selectedCallee = bundle.methodsByEntryId[selectedTarget];
        const selectedCalleeInstanceId = `${callId}/target:${selectedTargetIndex}:${selectedTarget}`;
        branchGroups.push({
          id: callId,
          instanceId,
          definitionBranchId: `dispatch:${node.id}`,
          kind: "DISPATCH",
          method: method.methodFullName,
          line: node.line,
          arms: targets.map((targetEntryId) => {
            const callee = bundle.methodsByEntryId[targetEntryId];
            return {
              label: targetEntryId,
              empty: !callee,
              exits: [{ kind: "continues" as const }],
              conditionCode: callee?.methodFullName,
            };
          }),
          selectedArmLabel: selectedTarget,
          entryPredecessorIds: [],
          entrySuccessorIds: [],
          decisionPredecessorIds: [],
          exitPredecessorIds: [],
          continuationIds: [],
          dispatchAnchorId: selectedCallee && !activeMethods.has(selectedTarget)
            ? instanceNodeId(selectedCalleeInstanceId, selectedCallee.entryId)
            : undefined,
        });
      }
      for (const [targetIndex, targetEntryId] of targets.entries()) {
        if (targetEntryId !== selectedTarget) continue;
        if (activeMethods.has(targetEntryId)) continue;
        const callee = bundle.methodsByEntryId[targetEntryId];
        if (!callee) continue;
        const childInstanceId = `${callId}/target:${targetIndex}:${targetEntryId}`;
        const childActiveMethods = new Set(activeMethods);
        childActiveMethods.add(targetEntryId);
        instantiate(
          targetEntryId,
          childInstanceId,
          depth + 1,
          childActiveMethods,
          visibleNode.retainedCall ? undefined : visibleNode.phase,
        );
        edges.push({
          from: id,
          to: instanceNodeId(childInstanceId, callee.entryId),
          type: "invoke",
          kind: "invoke",
        });
      }
    }

    // Expansion never changes the caller's local control flow.
    for (const edge of method.sequenceEdges) {
      edges.push({
        ...edge,
        from: instanceNodeId(instanceId, edge.from),
        to: instanceNodeId(instanceId, edge.to),
        branchRequirements: edge.branchRequirements
          ? instantiateRequirements(instanceId, edge.branchRequirements)
          : undefined,
        kind: "sequence",
      });
    }
  };

  const rootInstanceId = `operation:${operationId}/root:${projectionRootEntryId}`;
  instantiate(projectionRootEntryId, rootInstanceId, 0, new Set([projectionRootEntryId]));
  // Edge requirements are the execution contract. Node branch memberships
  // describe containment for branch panels, but must not independently hide
  // a node: an empty arm may continue through an edge whose source is also
  // the non-empty arm's structural branch point. A root-forward walk over
  // allowed edges matches the selected root-forward route's visibility and
  // keeps common flow after convergence reachable.
  const requirementFilteredEdges = edges.filter((edge) =>
    emittedNodes.has(edge.from)
    && emittedNodes.has(edge.to)
    && requirementsMatch(edge.branchRequirements, resolvedBranchArms));
  const structureNodeIds = new Set(
    nodes
      .filter((node) => node.node.type === "structure")
      .map((node) => node.id),
  );
  const conditionAliasNodeIds = new Set(
    branchGroups.flatMap((group) =>
      (group.conditionStages ?? []).flatMap((stage) => stage.nodeIds)),
  );
  const hiddenNodeIds = new Set([...structureNodeIds, ...conditionAliasNodeIds]);
  const sourceNodeById = new Map(nodes.map((node) => [node.id, node]));
  for (const group of branchGroups) {
    for (const stage of group.conditionStages ?? []) {
      stage.code = stage.nodeIds
        .map((nodeId) => sourceNodeById.get(nodeId)?.node.code)
        .find((code): code is string => Boolean(code));
    }
  }
  const incomingByNode = new Map<string, string[]>();
  const outgoingByNode = new Map<string, string[]>();
  const unfilteredIncomingByNode = new Map<string, string[]>();
  for (const edge of edges) {
    const incoming = unfilteredIncomingByNode.get(edge.to);
    if (incoming) incoming.push(edge.from);
    else unfilteredIncomingByNode.set(edge.to, [edge.from]);
  }
  for (const edge of requirementFilteredEdges) {
    const incoming = incomingByNode.get(edge.to);
    if (incoming) incoming.push(edge.from);
    else incomingByNode.set(edge.to, [edge.from]);
    const outgoing = outgoingByNode.get(edge.from);
    if (outgoing) outgoing.push(edge.to);
    else outgoingByNode.set(edge.from, [edge.to]);
  }
  const visibleBoundary = (
    startId: string,
    adjacency: ReadonlyMap<string, string[]>,
  ): string[] => {
    const result = new Set<string>();
    const visited = new Set<string>();
    const pending = [...(adjacency.get(startId) ?? [])];
    while (pending.length > 0) {
      const nodeId = pending.pop()!;
      if (visited.has(nodeId)) continue;
      visited.add(nodeId);
      if (hiddenNodeIds.has(nodeId)) {
        pending.push(...(adjacency.get(nodeId) ?? []));
      } else {
        result.add(nodeId);
      }
    }
    return [...result];
  };
  const tryDecisionNodeId = (group: VisibleBranchGroup): string | undefined => {
    if (group.kind !== "TRY") return undefined;
    const outcomesByStructuralSource = new Map<string, Set<string>>();
    for (const edge of edges) {
      if (!hiddenNodeIds.has(edge.from)) continue;
      for (const requirement of edge.branchRequirements ?? []) {
        if (requirement.groupId !== group.id) continue;
        const outcomes = outcomesByStructuralSource.get(edge.from) ?? new Set<string>();
        outcomes.add(requirement.armLabel);
        outcomesByStructuralSource.set(edge.from, outcomes);
      }
    }
    return [...outcomesByStructuralSource.entries()]
      .sort((left, right) => right[1].size - left[1].size)[0]?.[0];
  };
  const bridgedBranchGroups = branchGroups.map((group) => {
    const decisionNodeId = tryDecisionNodeId(group);
    return {
      ...group,
      entryPredecessorIds: group.entryNodeId
        ? visibleBoundary(group.entryNodeId, incomingByNode)
        : [],
      entrySuccessorIds: group.entryNodeId
        ? visibleBoundary(group.entryNodeId, outgoingByNode)
        : [],
      // Catch dispatch is a definition-level boundary. Derive it before arm
      // filtering so nested branch choices cannot move the TRY control back
      // to its entry or make the anchor disappear.
      decisionPredecessorIds: decisionNodeId
        ? visibleBoundary(decisionNodeId, unfilteredIncomingByNode)
        : [],
      exitPredecessorIds: group.exitNodeId
        ? visibleBoundary(group.exitNodeId, incomingByNode)
        : [],
      continuationIds: group.exitNodeId
        ? visibleBoundary(group.exitNodeId, outgoingByNode)
        : [],
    };
  });
  const bridged = bridgeHiddenNodes(nodes, requirementFilteredEdges, hiddenNodeIds);
  const bridgedNodeIds = new Set(bridged.nodes.map((node) => node.id));
  const outgoing = new Map<string, string[]>();
  for (const edge of bridged.edges) {
    const targets = outgoing.get(edge.from);
    if (targets) targets.push(edge.to);
    else outgoing.set(edge.from, [edge.to]);
  }
  const rootId = instanceNodeId(rootInstanceId, projectionRootEntryId);
  const reachableNodeIds = new Set<string>();
  const pending = [rootId];
  while (pending.length > 0) {
    const nodeId = pending.pop()!;
    if (reachableNodeIds.has(nodeId) || !bridgedNodeIds.has(nodeId)) continue;
    reachableNodeIds.add(nodeId);
    pending.push(...(outgoing.get(nodeId) ?? []));
  }
  const visibleNodes = bridged.nodes.filter((node) => reachableNodeIds.has(node.id));
  const visibleEdges = bridged.edges.filter((edge) =>
    reachableNodeIds.has(edge.from) && reachableNodeIds.has(edge.to));
  // Entry/exit nodes are structural and have no backend phase membership.
  // Attach them to the phase that actually executes beside them after branch
  // filtering. Never use the definition's first/last ordinal phase here: its
  // members may belong to an unselected arm and be completely invisible.
  const visibleNodeById = new Map(visibleNodes.map((node) => [node.id, node]));
  for (let pass = 0; pass < 2; pass++) {
    for (const node of visibleNodes) {
      if (node.phase || (node.node.type !== "entry" && node.node.type !== "exit")) continue;
      const adjacentIds = node.node.type === "entry"
        ? visibleEdges.filter((edge) => edge.kind === "sequence" && edge.from === node.id).map((edge) => edge.to)
        : visibleEdges.filter((edge) => edge.kind === "sequence" && edge.to === node.id).map((edge) => edge.from);
      const adjacentPhase = adjacentIds
        .map((id) => visibleNodeById.get(id)?.phase)
        .find((phase): phase is VisiblePhase => phase !== undefined);
      if (adjacentPhase) node.phase = adjacentPhase;
    }
  }
  const visibleExits = exits.filter((exit) =>
    reachableNodeIds.has(exit.sourceNodeId)
    && requirementsMatch(exit.branchRequirements, resolvedBranchArms));

  return {
    rootId,
    nodes: visibleNodes,
    edges: visibleEdges,
    branchGroups: bridgedBranchGroups,
    exits: visibleExits,
  };
}
