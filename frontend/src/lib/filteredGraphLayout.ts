import type {
  VisibleBranchArm,
  VisibleBranchGroup,
  VisibleGraphProjection,
  VisibleNode,
} from "./filteredGraphProjection";
import { shortClassName } from "./graph";

export interface GraphPoint { x: number; y: number }

export interface BranchGeometry {
  group: VisibleBranchGroup;
  x: number;
  y: number;
  width: number;
  height: number;
  /** Exact semantic content used to size this rectangle. */
  ownedNodeIds: ReadonlySet<string>;
  compactEmpty: boolean;
}

export interface PhaseGeometry {
  id: string;
  label: string;
  colorIndex: number;
  depth: number;
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface FilteredGraphLayout {
  positions: Map<string, GraphPoint>;
  branches: BranchGeometry[];
  phases: PhaseGeometry[];
  width: number;
  height: number;
}

export const FILTERED_COLUMN_WIDTH = 260;
export const FILTERED_ROW_HEIGHT = 78;
export const FILTERED_PAD_X = 80;
export const FILTERED_PAD_Y = 58;

const BRANCH_REGION_PAD = 30;
const EMPTY_BRANCH_HEIGHT = 46;
const BRANCH_STACK_GAP = 10;
const EMPTY_PANEL_NODE_GAP = 12;
const NODE_LABEL_CHAR_WIDTH = 6.6;

function nodeRadius(node: VisibleNode): number {
  if (node.node.type === "entry") return 13;
  if (node.node.type === "call") return 10;
  return 8;
}

export function branchArmText(arm: VisibleBranchArm): string {
  const detail = arm.conditionCode
    ?? (arm.exceptionType ? `catch ${arm.exceptionType}` : undefined);
  const meaning = arm.empty && arm.terminus ? `empty → ${arm.terminus}` : undefined;
  return [arm.label, detail, meaning].filter(Boolean).join(" · ");
}

export function truncateBranchText(text: string, length = 34): string {
  return text.length > length ? `${text.slice(0, length - 1)}…` : text;
}

export function dispatchArmLabel(arm: VisibleBranchArm): string {
  return arm.conditionCode ? shortClassName(arm.conditionCode) : arm.label;
}

export function dispatchArmWidth(arm: VisibleBranchArm): number {
  return Math.max(58, dispatchArmLabel(arm).length * 6.2 + 22);
}

function fullNodeLabel(node: VisibleNode): string {
  return node.node.code ?? node.node.calleeFullName ?? node.definitionNodeId;
}

function entryShortName(fullName: string): string {
  const qualified = fullName.split(":", 1)[0];
  const parts = qualified.split(".");
  const name = parts.pop() ?? qualified;
  const className = parts.pop();
  return className ? `${className}.${name}` : name;
}

export function visibleNodeLabel(node: VisibleNode): string {
  if (node.node.type === "exit" && node.node.exitKind === "fallthrough") return "return";
  if (node.node.type === "entry" && node.node.calleeFullName) {
    return entryShortName(node.node.calleeFullName);
  }
  return fullNodeLabel(node).slice(0, 48);
}

/**
 * Place every instantiated method as a contiguous block. An expanded child
 * is emitted immediately after its owning call, so the caller's subsequent
 * operation cannot share rows with that expansion or a later expansion.
 */
function layoutInstanceBlocks(projection: VisibleGraphProjection): Map<string, GraphPoint> {
  const nodesByInstance = new Map<string, VisibleNode[]>();
  for (const node of projection.nodes) {
    const nodes = nodesByInstance.get(node.instanceId);
    if (nodes) nodes.push(node);
    else nodesByInstance.set(node.instanceId, [node]);
  }

  const nodeById = new Map(projection.nodes.map((node) => [node.id, node]));
  const childrenByCall = new Map<string, string[]>();
  const childInstances = new Set<string>();
  const entryByInstance = new Map<string, string>();
  for (const edge of projection.edges) {
    if (edge.kind !== "invoke") continue;
    const child = nodeById.get(edge.to);
    if (!child) continue;
    childInstances.add(child.instanceId);
    entryByInstance.set(child.instanceId, child.id);
    const children = childrenByCall.get(edge.from);
    if (children) {
      if (!children.includes(child.instanceId)) children.push(child.instanceId);
    } else {
      childrenByCall.set(edge.from, [child.instanceId]);
    }
  }

  const root = nodeById.get(projection.rootId);
  if (root) entryByInstance.set(root.instanceId, root.id);

  /**
   * Projection nodes retain definition serialization order, which is not an
   * execution order after an arm has been selected. In particular, a TRY's
   * common continuation can be serialized before its catch body. Lay out the
   * surviving graph by traversing its selected sequence edges instead.
   */
  const orderedNodes = (instanceId: string): VisibleNode[] => {
    const localNodes = nodesByInstance.get(instanceId) ?? [];
    const localIds = new Set(localNodes.map((node) => node.id));
    const outgoing = new Map<string, string[]>();
    for (const edge of projection.edges) {
      if (edge.kind !== "sequence" || !localIds.has(edge.from) || !localIds.has(edge.to)) continue;
      const targets = outgoing.get(edge.from);
      if (targets) {
        if (!targets.includes(edge.to)) targets.push(edge.to);
      } else {
        outgoing.set(edge.from, [edge.to]);
      }
    }

    const result: VisibleNode[] = [];
    const visited = new Set<string>();
    const visit = (id: string): void => {
      if (visited.has(id)) return;
      const current = nodeById.get(id);
      if (!current || current.instanceId !== instanceId) return;
      visited.add(id);
      result.push(current);
      for (const target of outgoing.get(id) ?? []) visit(target);
    };

    const entryId = entryByInstance.get(instanceId)
      ?? localNodes.find((node) => node.node.type === "entry")?.id;
    if (entryId) visit(entryId);
    // Keep malformed or intentionally disconnected visible nodes inspectable.
    for (const localNode of localNodes) visit(localNode.id);
    return result;
  };

  const positions = new Map<string, GraphPoint>();
  const placedInstances = new Set<string>();
  let row = 0;
  const placeInstance = (instanceId: string): void => {
    if (placedInstances.has(instanceId)) return;
    placedInstances.add(instanceId);
    for (const node of orderedNodes(instanceId)) {
      positions.set(node.id, {
        x: FILTERED_PAD_X + node.depth * FILTERED_COLUMN_WIDTH,
        y: FILTERED_PAD_Y + row * FILTERED_ROW_HEIGHT,
      });
      row++;
      for (const childInstance of childrenByCall.get(node.id) ?? []) {
        placeInstance(childInstance);
      }
    }
  };

  if (root) placeInstance(root.instanceId);
  for (const instanceId of nodesByInstance.keys()) {
    if (!childInstances.has(instanceId)) placeInstance(instanceId);
  }
  for (const instanceId of nodesByInstance.keys()) placeInstance(instanceId);
  return positions;
}

function selectedRouteEdges(projection: VisibleGraphProjection, group: VisibleBranchGroup) {
  return projection.edges.filter((edge) =>
    edge.branchRequirements?.some((requirement) =>
      requirement.groupId === group.id
      && requirement.armLabel === group.selectedArmLabel));
}

function expandedDescendants(
  projection: VisibleGraphProjection,
  directMembers: ReadonlySet<string>,
): Set<string> {
  const descendants = new Set<string>();
  for (const node of projection.nodes) {
    if (!directMembers.has(node.id) || !node.expanded) continue;
    const childPrefix = `${node.instanceId}/call:${node.definitionNodeId}/target:`;
    for (const candidate of projection.nodes) {
      if (candidate.instanceId.startsWith(childPrefix)) descendants.add(candidate.id);
    }
  }
  return descendants;
}

/** Explicit ownership replaces accidental "anything inside the rectangle". */
function ownedNodesForBranch(
  projection: VisibleGraphProjection,
  group: VisibleBranchGroup,
): Set<string> {
  const owned = new Set(
    projection.nodes
      .filter((node) => node.branchRequirements.some((requirement) =>
        requirement.groupId === group.id
        && requirement.armLabel === group.selectedArmLabel))
      .map((node) => node.id),
  );

  // Exit requirements are authoritative for return-only and throw-only arms.
  for (const exit of projection.exits) {
    if (exit.branchRequirements?.some((requirement) =>
      requirement.groupId === group.id
      && requirement.armLabel === group.selectedArmLabel)) {
      owned.add(exit.sourceNodeId);
    }
  }

  for (const descendantId of expandedDescendants(projection, owned)) {
    owned.add(descendantId);
  }
  return owned;
}

interface BranchCandidate {
  group: VisibleBranchGroup;
  sourceIndex: number;
  forkY: number;
  headY?: number;
  headClearance: number;
  desiredY: number;
  x: number;
  controlsWidth: number;
  ownedNodeIds: Set<string>;
  compactEmpty: boolean;
}

function buildBranchCandidates(
  projection: VisibleGraphProjection,
  positions: ReadonlyMap<string, GraphPoint>,
  nodeById: ReadonlyMap<string, VisibleNode>,
): BranchCandidate[] {
  return projection.branchGroups.flatMap((group, sourceIndex) => {
    // Dispatch is an inline selector attached to the selected callee entry,
    // not a branch region that owns or reserves graph rows.
    if (group.kind === "DISPATCH") return [];
    const selectedArm = group.arms.find((arm) => arm.label === group.selectedArmLabel);
    if (!selectedArm) return [];
    const forkEntry = (group.branchPointIds ?? [])
      .map((id) => ({ id, point: positions.get(id) }))
      .find((entry): entry is { id: string; point: GraphPoint } => entry.point !== undefined);
    if (!forkEntry) return [];
    const fork = forkEntry.point;

    // Expansion rows belong to the execution of the branch-point call. The
    // branch decision panel must follow that complete child block, rather
    // than float over it using the caller call's original row as its anchor.
    const forkNode = nodeById.get(forkEntry.id);
    const childPrefix = forkNode?.expanded
      ? `${forkNode.instanceId}/call:${forkNode.definitionNodeId}/target:`
      : undefined;
    const expandedForkNodes = childPrefix
      ? projection.nodes.filter((node) => node.instanceId.startsWith(childPrefix))
      : [];
    const expandedForkBottom = expandedForkNodes.reduce((bottom, node) => {
      const point = positions.get(node.id);
      return point
        ? Math.max(bottom, point.y + nodeRadius(node) + EMPTY_PANEL_NODE_GAP)
        : bottom;
    }, fork.y);

    const routeEdges = selectedRouteEdges(projection, group);
    const ownedNodeIds = ownedNodesForBranch(projection, group);
    // A shared surviving predecessor does not make a later branch reachable.
    const selectedTargetIds = [
      ...(selectedArm.targetIds ?? []),
      ...(selectedArm.exits ?? []).flatMap((exit) => exit.targetIds ?? []),
    ];
    if (
      ownedNodeIds.size === 0
      && routeEdges.length === 0
      && !selectedTargetIds.some((id) => positions.has(id))
    ) return [];

    const targetIds = [
      ...selectedTargetIds,
      ...routeEdges.map((edge) => edge.to),
    ];
    const firstOwnedId = [...ownedNodeIds]
      .filter((id) => positions.has(id))
      .sort((left, right) => positions.get(left)!.y - positions.get(right)!.y)[0];
    const effectiveHeadId = selectedArm.firstCallId
      ?? firstOwnedId
      ?? targetIds.find((id) => positions.has(id));
    const head = effectiveHeadId ? positions.get(effectiveHeadId) : undefined;
    const headNode = effectiveHeadId ? nodeById.get(effectiveHeadId) : undefined;
    const ownedPoints = [...ownedNodeIds].flatMap((id) => {
      const point = positions.get(id);
      return point ? [point] : [];
    });
    const compactEmpty = selectedArm.empty && ownedPoints.length === 0;
    const minOwnedX = ownedPoints.length > 0
      ? Math.min(...ownedPoints.map((point) => point.x))
      : head?.x ?? fork.x;
    // Empty-arm controls have no owned content to centre around. Anchoring
    // them to the midpoint between fork and continuation creates a feedback
    // loop when several controls share those endpoints: shifting the head
    // also shifts every desired midpoint, so the collision pass can never
    // close the gap. Keep compact controls directly below their fork instead;
    // subsequent controls stack from this stable anchor and the continuation
    // is shifted only as far as the complete stack requires.
    const desiredY = compactEmpty
      ? expandedForkBottom + EMPTY_PANEL_NODE_GAP
      : head
        ? expandedForkBottom + (head.y - expandedForkBottom) / 2
        : expandedForkBottom + FILTERED_ROW_HEIGHT / 2;
    const controlsWidth = 78 + group.arms.reduce(
      (width, arm) => width
        + Math.max(58, truncateBranchText(branchArmText(arm)).length * 6.2 + 18)
        + 6,
      0,
    );
    return [{
      group,
      sourceIndex,
      forkY: expandedForkBottom,
      headY: head?.y,
      headClearance: (headNode ? nodeRadius(headNode) : 0) + EMPTY_PANEL_NODE_GAP,
      desiredY,
      x: Math.min(fork.x, minOwnedX) - BRANCH_REGION_PAD,
      controlsWidth,
      ownedNodeIds,
      compactEmpty,
    }];
  });
}

function shiftRows(
  positions: Map<string, GraphPoint>,
  threshold: number,
  amount: number,
): void {
  for (const [nodeId, point] of positions) {
    if (point.y >= threshold) positions.set(nodeId, { ...point, y: point.y + amount });
  }
}

function placeBranchPanels(
  projection: VisibleGraphProjection,
  positions: Map<string, GraphPoint>,
  nodeById: ReadonlyMap<string, VisibleNode>,
): BranchGeometry[] {
  const sortedCandidates = (): BranchCandidate[] =>
    buildBranchCandidates(projection, positions, nodeById).sort((left, right) =>
      left.group.instanceId.localeCompare(right.group.instanceId)
      // A TRY's source line is the start of its protected body, but its
      // catch/noCatch split hangs from the body tail. Order every structure by
      // that actual fork position, exactly as we do for IF execution order.
      || left.forkY - right.forkY
      || (left.group.line ?? Number.MAX_SAFE_INTEGER) - (right.group.line ?? Number.MAX_SAFE_INTEGER)
      || left.sourceIndex - right.sourceIndex);
  let candidates = sortedCandidates();

  const laneFor = (candidate: BranchCandidate): string => {
    const candidateMembers = candidate.ownedNodeIds;
    const parents = candidates.filter((possibleParent) => {
      if (
        possibleParent.group.id === candidate.group.id
        || possibleParent.group.instanceId !== candidate.group.instanceId
      ) return false;
      if (candidateMembers.size > 0) {
        return possibleParent.ownedNodeIds.size > candidateMembers.size
          && [...candidateMembers].every((id) => possibleParent.ownedNodeIds.has(id));
      }
      return (candidate.group.branchPointIds ?? []).some((id) =>
        possibleParent.ownedNodeIds.has(id));
    });
    const parent = parents.sort(
      (left, right) => left.ownedNodeIds.size - right.ownedNodeIds.size,
    )[0];
    return `${candidate.group.instanceId}:${parent?.group.id ?? "root"}`;
  };

  // Row reservation can alter later fork/head positions. Stabilize those
  // positions iteratively with a hard cap so malformed geometry cannot hang
  // the UI through unbounded recursive restarts.
  const maxPasses = Math.max(8, candidates.length * 4);
  for (let pass = 0; pass < maxPasses; pass++) {
    const nextYByLane = new Map<string, number>();
    let shifted = false;
    for (const candidate of candidates) {
      const lane = laneFor(candidate);
      const minimumY = nextYByLane.get(lane) ?? -Infinity;
      const y = Math.max(candidate.desiredY, minimumY);
      if (
        candidate.compactEmpty
        && candidate.headY !== undefined
        && y + EMPTY_BRANCH_HEIGHT + candidate.headClearance > candidate.headY
      ) {
        const overlap = y
          + EMPTY_BRANCH_HEIGHT
          + candidate.headClearance
          - candidate.headY;
        shiftRows(positions, candidate.headY, overlap);
        shifted = true;
        break;
      }
      if (
        !candidate.compactEmpty
        &&
        candidate.desiredY < minimumY
        && candidate.headY !== undefined
        && candidate.headY > candidate.forkY
      ) {
        shiftRows(positions, candidate.headY, 2 * (minimumY - candidate.desiredY));
        shifted = true;
        break;
      }
      const ownedBottom = [...candidate.ownedNodeIds].reduce((bottom, id) => {
        const point = positions.get(id);
        return point ? Math.max(bottom, point.y + BRANCH_REGION_PAD) : bottom;
      }, y + EMPTY_BRANCH_HEIGHT);
      const bottom = candidate.compactEmpty ? y + EMPTY_BRANCH_HEIGHT : ownedBottom;
      nextYByLane.set(lane, bottom + BRANCH_STACK_GAP);
    }
    if (!shifted) break;
    candidates = sortedCandidates();
  }

  const nextYByLane = new Map<string, number>();
  return candidates.map((candidate) => {
    const lane = laneFor(candidate);
    const y = Math.max(candidate.desiredY, nextYByLane.get(lane) ?? -Infinity);
    const ownedPoints = [...candidate.ownedNodeIds].flatMap((id) => {
      const point = positions.get(id);
      return point ? [point] : [];
    });
    const bottom = candidate.compactEmpty
      ? y + EMPTY_BRANCH_HEIGHT
      : Math.max(y + EMPTY_BRANCH_HEIGHT, ...ownedPoints.map((point) => point.y + BRANCH_REGION_PAD));
    let right = candidate.x + candidate.controlsWidth;
    for (const nodeId of candidate.ownedNodeIds) {
      const node = nodeById.get(nodeId);
      const point = positions.get(nodeId);
      if (!node || !point) continue;
      const labelWidth = nodeRadius(node) + 9
        + visibleNodeLabel(node).length * NODE_LABEL_CHAR_WIDTH;
      right = Math.max(right, point.x + labelWidth + BRANCH_REGION_PAD);
    }
    nextYByLane.set(lane, bottom + BRANCH_STACK_GAP);
    return {
      group: candidate.group,
      x: candidate.x,
      y,
      width: right - candidate.x,
      height: bottom - y,
      ownedNodeIds: candidate.ownedNodeIds,
      compactEmpty: candidate.compactEmpty,
    };
  });
}

export function layoutFilteredGraph(projection: VisibleGraphProjection): FilteredGraphLayout {
  const positions = layoutInstanceBlocks(projection);
  const nodeById = new Map(projection.nodes.map((node) => [node.id, node]));
  const branches = placeBranchPanels(projection, positions, nodeById);
  const nodesByInstance = new Map<string, VisibleNode[]>();
  const phaseMembers = new Map<string, VisibleNode[]>();
  for (const node of projection.nodes) {
    const instanceNodes = nodesByInstance.get(node.instanceId);
    if (instanceNodes) instanceNodes.push(node);
    else nodesByInstance.set(node.instanceId, [node]);
    if (!node.phase) continue;
    const members = phaseMembers.get(node.phase.id);
    if (members) members.push(node);
    else phaseMembers.set(node.phase.id, [node]);
  }
  const phases: PhaseGeometry[] = [];
  for (const [phaseId, members] of phaseMembers) {
    const ordered = members
      .filter((node) => positions.has(node.id))
      .sort((left, right) => positions.get(left.id)!.y - positions.get(right.id)!.y);
    if (ordered.length === 0) continue;
    const instanceId = ordered[0].instanceId;
    const instanceNodes = (nodesByInstance.get(instanceId) ?? [])
      .filter((node) => positions.has(node.id));
    const instanceYs = instanceNodes.map((node) => positions.get(node.id)!.y);
    const phase = ordered[0].phase!;
    const runs: VisibleNode[][] = [];
    for (const node of ordered) {
      const current = runs[runs.length - 1];
      const previous = current?.[current.length - 1];
      if (
        !previous
        || (
          // Structural nodes decorate the first/final semantic phase; they
          // must never manufacture a separate entry-only or exit-only run
          // merely because layout panels inserted vertical space.
          previous.node.type !== "entry"
          && node.node.type !== "exit"
          // A non-retained callee inherits this phase. Its structural entry
          // and exit occupy rows between phase-member calls.
          && positions.get(node.id)!.y - positions.get(previous.id)!.y > FILTERED_ROW_HEIGHT * 2.5
        )
      ) runs.push([node]);
      else current.push(node);
    }
    const instanceTop = Math.min(...instanceYs);
    const instanceBottom = Math.max(...instanceYs);
    for (const [runIndex, run] of runs.entries()) {
      const first = positions.get(run[0].id)!;
      const last = positions.get(run[run.length - 1].id)!;
      // Entry and exit belong visually to the adjacent phase only when they
      // are actually adjacent. A retained call with an expanded callee creates
      // a larger gap and must remain an uncoloured boundary between methods.
      const top = runIndex === 0 && first.y - instanceTop <= FILTERED_ROW_HEIGHT * 1.5
        ? instanceTop - FILTERED_ROW_HEIGHT * 0.28
        : first.y - FILTERED_ROW_HEIGHT * 0.32;
      const bottom = runIndex === runs.length - 1
        && instanceBottom - last.y <= FILTERED_ROW_HEIGHT * 1.5
        ? instanceBottom + FILTERED_ROW_HEIGHT * 0.28
        : last.y + FILTERED_ROW_HEIGHT * 0.32;
      phases.push({
        id: `${phaseId}:${runIndex}`,
        label: phase.label ?? `Phase ${phase.index + 1}`,
        colorIndex: phase.index,
        depth: run[0].depth,
        x: first.x - 28,
        y: top,
        width: 218,
        height: bottom - top,
      });
    }
  }
  const maxDepth = Math.max(0, ...projection.nodes.map((node) => node.depth));
  const maxNodeY = Math.max(
    FILTERED_PAD_Y,
    ...[...positions.values()].map((point) => point.y),
  );
  const dispatchRightEdges = projection.branchGroups.flatMap((group) => {
    if (group.kind !== "DISPATCH") return [];
    const selectedArm = group.arms.find((arm) => arm.label === group.selectedArmLabel);
    const entry = selectedArm?.firstCallId ? positions.get(selectedArm.firstCallId) : undefined;
    const fallback = (group.branchPointIds ?? []).map((id) => positions.get(id)).find(Boolean);
    const anchor = entry ?? fallback;
    if (!anchor) return [];
    // Must mirror FilteredGraphSvg: label starts at anchor.x, pills begin 58
    // pixels later, and each pill is separated by a 6-pixel gap.
    const pillsWidth = group.arms.reduce(
      (total, arm, index) => total + dispatchArmWidth(arm) + (index > 0 ? 6 : 0),
      0,
    );
    return [anchor.x + 58 + pillsWidth];
  });
  const width = Math.max(
    FILTERED_PAD_X * 2 + (maxDepth + 1) * FILTERED_COLUMN_WIDTH,
    ...branches.map((branch) => branch.x + branch.width + FILTERED_PAD_X),
    ...dispatchRightEdges.map((right) => right + FILTERED_PAD_X),
  );
  const height = Math.max(
    maxNodeY + FILTERED_PAD_Y + FILTERED_ROW_HEIGHT,
    ...branches.map((branch) => branch.y + branch.height + FILTERED_PAD_Y),
  );
  return { positions, branches, phases, width, height };
}
