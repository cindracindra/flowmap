import type {
  ArmTerminus,
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
  kind: "return" | "throw" | "continues";
  frontierIds?: InstanceNodeId[];
  targetIds?: InstanceNodeId[];
  branchRequirements?: VisibleBranchRequirement[];
}

export interface VisibleBranchArm {
  label: string;
  firstCallId?: InstanceNodeId;
  empty: boolean;
  terminus?: ArmTerminus;
  exits?: VisibleArmExit[];
  conditionCode?: string;
  exceptionType?: string;
  targetIds?: InstanceNodeId[];
}

export interface VisibleBranchGroup {
  id: BranchInstanceId;
  instanceId: string;
  definitionBranchId: string;
  kind: string;
  method?: string;
  line?: number;
  arms: VisibleBranchArm[];
  selectedArmLabel: string;
  branchPointIds?: InstanceNodeId[];
  convergesAt?: InstanceNodeId;
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
        firstCallId: arm.firstCallId
          ? instanceNodeId(instanceId, arm.firstCallId)
          : undefined,
        empty: arm.empty,
        terminus: arm.terminus,
        exits: arm.exits?.map((exit) => ({
          kind: exit.kind,
          frontierIds: rewriteNodeIds(exit.frontierIds),
          targetIds: rewriteNodeIds(exit.targetIds),
          branchRequirements: exit.branchRequirements
            ? instantiateRequirements(instanceId, exit.branchRequirements)
            : undefined,
        })),
        conditionCode: arm.conditionCode,
        exceptionType: arm.exceptionType,
        targetIds: rewriteNodeIds(arm.targetIds),
      })),
      selectedArmLabel: selectedArm?.label ?? "",
      branchPointIds: rewriteNodeIds(group.branchPointIds),
      convergesAt: group.convergesAt
        ? instanceNodeId(instanceId, group.convergesAt)
        : undefined,
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
      (node) => node.line,
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
          ? bundle.methodsByEntryId[selectedTarget]?.phases.length
          : undefined,
        branchRequirements,
      };
      nodes.push(visibleNode);

      if (!visibleNode.expanded) continue;
      if (targets.length > 1 && selectedTarget) {
        branchGroups.push({
          id: callId,
          instanceId,
          definitionBranchId: `dispatch:${node.id}`,
          kind: "DISPATCH",
          method: method.methodFullName,
          line: node.line,
          arms: targets.map((targetEntryId, targetIndex) => {
            const callee = bundle.methodsByEntryId[targetEntryId];
            const childInstanceId = `${callId}/target:${targetIndex}:${targetEntryId}`;
            return {
              label: targetEntryId,
              firstCallId: callee
                ? instanceNodeId(childInstanceId, callee.entryId)
                : undefined,
              empty: !callee,
              terminus: "continues",
              targetIds: callee
                ? [instanceNodeId(childInstanceId, callee.entryId)]
                : [],
              conditionCode: callee?.methodFullName,
            };
          }),
          selectedArmLabel: selectedTarget,
          branchPointIds: [id],
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
  // allowed edges matches the anchored graph's visibility semantics and
  // keeps common flow after convergence reachable.
  const requirementFilteredEdges = edges.filter((edge) =>
    emittedNodes.has(edge.from)
    && emittedNodes.has(edge.to)
    && requirementsMatch(edge.branchRequirements, resolvedBranchArms));
  const outgoing = new Map<string, string[]>();
  for (const edge of requirementFilteredEdges) {
    const targets = outgoing.get(edge.from);
    if (targets) targets.push(edge.to);
    else outgoing.set(edge.from, [edge.to]);
  }
  const rootId = instanceNodeId(rootInstanceId, projectionRootEntryId);
  const reachableNodeIds = new Set<string>();
  const pending = [rootId];
  while (pending.length > 0) {
    const nodeId = pending.pop()!;
    if (reachableNodeIds.has(nodeId) || !emittedNodes.has(nodeId)) continue;
    reachableNodeIds.add(nodeId);
    pending.push(...(outgoing.get(nodeId) ?? []));
  }
  const visibleNodes = nodes.filter((node) => reachableNodeIds.has(node.id));
  const visibleEdges = requirementFilteredEdges.filter((edge) =>
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
    branchGroups,
    exits: visibleExits,
  };
}
