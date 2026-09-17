import type {
  VisibleBranchArm,
  VisibleBranchGroup,
  VisibleGraphProjection,
  VisibleNode,
} from "./filteredGraphProjection";
import { shortClassName } from "./graph";
import { sequenceOrderedNodes } from "./sequenceTraversal";

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
export const DISPATCH_SELECTOR_WIDTH = 360;

const BRANCH_REGION_PAD = 30;
const EMPTY_BRANCH_HEIGHT = 46;
const BRANCH_STACK_GAP = 10;
const EMPTY_PANEL_NODE_GAP = 12;
const NODE_LABEL_CHAR_WIDTH = 6.6;
const INSTANCE_HORIZONTAL_GAP = 42;
const SUBTREE_VERTICAL_GAP = 20;
const INVOKE_ENTRY_DROP = 46;

function nodeRadius(node: VisibleNode): number {
  if (node.node.type === "entry") return 13;
  if (node.node.type === "call") return 10;
  return 8;
}

export function branchArmText(arm: VisibleBranchArm): string {
  const detail = arm.conditionCode
    ?? (arm.exceptionType ? `catch ${arm.exceptionType}` : undefined);
  const exitKinds = [...new Set(arm.exits.map((exit) => exit.kind))];
  const meaning = arm.empty && exitKinds.length > 0
    ? `empty → ${exitKinds.join("/")}`
    : undefined;
  return [arm.label, detail, meaning].filter(Boolean).join(" · ");
}

export function truncateBranchText(text: string, length = 34): string {
  return text.length > length ? `${text.slice(0, length - 1)}…` : text;
}

export function branchArmToggleLabel(arm: VisibleBranchArm): string {
  return truncateBranchText(arm.label, 9);
}

export function branchArmToggleWidth(arm: VisibleBranchArm): number {
  return Math.max(42, branchArmToggleLabel(arm).length * 6.2 + 18);
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

interface CollisionRect {
  left: number;
  right: number;
  top: number;
  bottom: number;
}

interface InstanceBlock {
  positions: Map<string, GraphPoint>;
  collisionRects: CollisionRect[];
  entryId?: string;
}

function translateRect(rect: CollisionRect, dx: number, dy: number): CollisionRect {
  return {
    left: rect.left + dx,
    right: rect.right + dx,
    top: rect.top + dy,
    bottom: rect.bottom + dy,
  };
}

function nodeCollisionRect(node: VisibleNode, point: GraphPoint): CollisionRect {
  const radius = nodeRadius(node);
  const hasSecondLine = node.retainedCall
    || node.node.type === "exit"
    || node.node.type === "transfer";
  return {
    // Loop membership icons sit to the left of the node marker.
    left: point.x - radius - 22,
    right: point.x + radius + 15
      + visibleNodeLabel(node).length * NODE_LABEL_CHAR_WIDTH,
    top: point.y - radius - 8,
    bottom: point.y + (hasSecondLine ? 27 : radius + 8),
  };
}

function verticalTranslationWithoutCollisions(
  candidateRects: readonly CollisionRect[],
  occupiedRects: readonly CollisionRect[],
  preferredDy: number,
): number {
  let dy = preferredDy;
  // Moving the whole subtree down can expose a collision with a later
  // rectangle. Iterate to a fixed point rather than depending on input order.
  for (let pass = 0; pass <= occupiedRects.length; pass++) {
    let requiredDy = dy;
    for (const candidate of candidateRects) {
      const translatedTop = candidate.top + dy;
      const translatedBottom = candidate.bottom + dy;
      for (const occupied of occupiedRects) {
        const overlapsHorizontally = candidate.left < occupied.right
          && candidate.right > occupied.left;
        const overlapsVertically = translatedTop < occupied.bottom + SUBTREE_VERTICAL_GAP
          && translatedBottom > occupied.top - SUBTREE_VERTICAL_GAP;
        if (!overlapsHorizontally || !overlapsVertically) continue;
        requiredDy = Math.max(
          requiredDy,
          occupied.bottom + SUBTREE_VERTICAL_GAP - candidate.top,
        );
      }
    }
    if (requiredDy === dy) return dy;
    dy = requiredDy;
  }
  return dy;
}

/**
 * Lay out each method's own sequence independently. Expanded callees are
 * recursively packed as rectangles to the right of the caller and never
 * consume rows in the caller's sequence. This keeps the local continuation
 * compact while preventing sibling expansion subtrees from overlapping.
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
    const entryId = entryByInstance.get(instanceId)
      ?? localNodes.find((node) => node.node.type === "entry")?.id;
    return sequenceOrderedNodes(
      localNodes,
      projection.edges.filter((edge) =>
        edge.kind === "sequence"
        && localIds.has(edge.from)
        && localIds.has(edge.to)),
      entryId,
    );
  };

  const dispatchAnchorIds = new Set(
    projection.branchGroups.flatMap((group) =>
      group.kind === "DISPATCH" && group.dispatchAnchorId
        ? [group.dispatchAnchorId]
        : []),
  );
  const controlsWidthByInstance = new Map<string, number>();
  for (const group of projection.branchGroups) {
    if (group.kind === "DISPATCH") continue;
    const width = 78 + group.arms.reduce(
      (total, arm) => total + branchArmToggleWidth(arm) + 6,
      0,
    );
    controlsWidthByInstance.set(
      group.instanceId,
      Math.max(controlsWidthByInstance.get(group.instanceId) ?? 0, width),
    );
  }

  const builtInstances = new Map<string, InstanceBlock>();
  const buildingInstances = new Set<string>();
  const buildInstance = (instanceId: string): InstanceBlock => {
    const existing = builtInstances.get(instanceId);
    if (existing) return existing;
    // Projection recursion is cut off before an invoke edge is emitted, but
    // retain this guard so malformed input cannot recurse forever.
    if (buildingInstances.has(instanceId)) {
      return { positions: new Map(), collisionRects: [] };
    }
    buildingInstances.add(instanceId);

    const localNodes = orderedNodes(instanceId);
    const positions = new Map<string, GraphPoint>();
    const localRects: CollisionRect[] = [];
    for (const [row, node] of localNodes.entries()) {
      const point = { x: 0, y: row * FILTERED_ROW_HEIGHT };
      positions.set(node.id, point);
      localRects.push(nodeCollisionRect(node, point));
      if (dispatchAnchorIds.has(node.id)) {
        localRects.push({
          left: point.x,
          right: point.x + DISPATCH_SELECTOR_WIDTH,
          top: point.y - 46,
          bottom: point.y - 8,
        });
      }
    }

    const localRight = Math.max(
      0,
      ...localRects.map((rect) => rect.right),
      (controlsWidthByInstance.get(instanceId) ?? 0) + BRANCH_REGION_PAD,
    );
    const childRects: CollisionRect[] = [];
    for (const node of localNodes) {
      const callPoint = positions.get(node.id)!;
      for (const childInstanceId of childrenByCall.get(node.id) ?? []) {
        const child = buildInstance(childInstanceId);
        const childEntryPoint = child.entryId
          ? child.positions.get(child.entryId)
          : undefined;
        if (!childEntryPoint) continue;
        const childLeft = Math.min(...child.collisionRects.map((rect) => rect.left));
        const dx = Math.max(
          FILTERED_COLUMN_WIDTH - childEntryPoint.x,
          localRight + INSTANCE_HORIZONTAL_GAP - childLeft,
        );
        // Keep invocation edges visibly distinct from local sequence edges.
        // The callee starts a little below its call site, while remaining an
        // independently packed subtree that consumes no caller rows.
        const preferredDy = callPoint.y + INVOKE_ENTRY_DROP - childEntryPoint.y;
        const translatedForPacking = child.collisionRects.map((rect) =>
          translateRect(rect, dx, 0));
        const dy = verticalTranslationWithoutCollisions(
          translatedForPacking,
          childRects,
          preferredDy,
        );
        for (const [childNodeId, childPoint] of child.positions) {
          positions.set(childNodeId, {
            x: childPoint.x + dx,
            y: childPoint.y + dy,
          });
        }
        childRects.push(...child.collisionRects.map((rect) =>
          translateRect(rect, dx, dy)));
      }
    }

    const entryId = entryByInstance.get(instanceId)
      ?? localNodes.find((node) => node.node.type === "entry")?.id;
    const block = {
      positions,
      collisionRects: [...localRects, ...childRects],
      entryId,
    };
    buildingInstances.delete(instanceId);
    builtInstances.set(instanceId, block);
    return block;
  };

  const positions = new Map<string, GraphPoint>();
  const occupiedRootRects: CollisionRect[] = [];
  const placedInstances = new Set<string>();
  const placeRoot = (instanceId: string): void => {
    if (placedInstances.has(instanceId)) return;
    const block = buildInstance(instanceId);
    const dy = verticalTranslationWithoutCollisions(
      block.collisionRects,
      occupiedRootRects,
      FILTERED_PAD_Y,
    );
    for (const [nodeId, point] of block.positions) {
      positions.set(nodeId, { x: point.x + FILTERED_PAD_X, y: point.y + dy });
      const instance = nodeById.get(nodeId)?.instanceId;
      if (instance) placedInstances.add(instance);
    }
    occupiedRootRects.push(...block.collisionRects.map((rect) =>
      translateRect(rect, FILTERED_PAD_X, dy)));
  };

  if (root) placeRoot(root.instanceId);
  for (const instanceId of nodesByInstance.keys()) {
    if (!childInstances.has(instanceId)) placeRoot(instanceId);
  }
  // Defensive fallback for an orphaned or malformed instance tree.
  for (const instanceId of nodesByInstance.keys()) placeRoot(instanceId);
  return positions;
}

function selectedRouteEdges(projection: VisibleGraphProjection, group: VisibleBranchGroup) {
  return projection.edges.filter((edge) =>
    edge.branchRequirements?.some((requirement) =>
      requirement.groupId === group.id
      && requirement.armLabel === group.selectedArmLabel));
}

/** Explicit ownership replaces accidental "anything inside the rectangle". */
function ownedNodesForBranch(
  projection: VisibleGraphProjection,
  group: VisibleBranchGroup,
): Set<string> {
  const owned = new Set(
    projection.nodes
      .filter((node) =>
        node.instanceId === group.instanceId
        && node.branchRequirements.some((requirement) =>
          requirement.groupId === group.id
          && requirement.armLabel === group.selectedArmLabel))
      .map((node) => node.id),
  );

  // Exit requirements are authoritative for return-only and throw-only arms.
  for (const exit of projection.exits) {
    if (exit.instanceId === group.instanceId && exit.branchRequirements?.some((requirement) =>
      requirement.groupId === group.id
      && requirement.armLabel === group.selectedArmLabel)) {
      owned.add(exit.sourceNodeId);
    }
  }

  // Branch membership is local to the method instance that owns the control.
  // Expanded callees are connected subtrees, not contents of the caller's
  // lexical branch panel. Including them here makes a narrow caller panel
  // inherit the full height of a callee rendered in another column.
  return owned;
}

interface BranchCandidate {
  group: VisibleBranchGroup;
  sourceIndex: number;
  forkY: number;
  headId?: string;
  headY?: number;
  headClearance: number;
  desiredY: number;
  x: number;
  controlsWidth: number;
  ownedNodeIds: Set<string>;
  compactEmpty: boolean;
  minimumHeight: number;
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
    if (!group.decisionReachable) return [];
    const selectedArmByGroup = new Map(
      projection.branchGroups.map((candidate) => [candidate.id, candidate.selectedArmLabel]),
    );
    if (!(group.enclosingRequirements ?? []).every((requirement) =>
      selectedArmByGroup.get(requirement.groupId) === requirement.armLabel)) return [];
    const selectedArm = group.arms.find((arm) => arm.label === group.selectedArmLabel);
    if (!selectedArm) return [];
    // Controls belong after condition evaluation. Prefer the visible
    // predecessors of the decision anchor for both IF and TRY, falling back
    // to the structure-entry boundary only when no decision is available.
    const forkIds = group.decisionPredecessorIds.length > 0
      ? group.decisionPredecessorIds
      : group.entryPredecessorIds;
    const forkEntry = forkIds
      .map((id) => ({ id, point: positions.get(id) }))
      .filter((entry): entry is { id: string; point: GraphPoint } => entry.point !== undefined)
      // A short-circuit condition can leave several visible predecessors for
      // one hidden decision. The decision occurs after its latest displayed
      // condition call, not after whichever predecessor happened to be
      // discovered first (often the method entry).
      .sort((left, right) => right.point.y - left.point.y)[0];
    if (!forkEntry) return [];
    const fork = forkEntry.point;

    const routeEdges = selectedRouteEdges(projection, group);
    const ownedNodeIds = ownedNodesForBranch(projection, group);
    // The visible structural boundary is the reachability proof for the
    // control itself. Do not drop the panel merely because the selected arm
    // has no surviving operation or continuation: terminal TRY catch arms
    // commonly end in a return/throw whose node has already been bridged out.
    const selectedTargetIds = group.continuationIds;

    const firstOwnedId = [...ownedNodeIds]
      .filter((id) => positions.has(id))
      .sort((left, right) => positions.get(left)!.y - positions.get(right)!.y)[0];
    const ownedPoints = [...ownedNodeIds].flatMap((id) => {
      const point = positions.get(id);
      return point ? [point] : [];
    });
    const compactEmpty = ownedPoints.length === 0;
    const continuationHeadId = selectedTargetIds.find((id) => {
      const point = positions.get(id);
      return point !== undefined && point.y > fork.y;
    }) ?? routeEdges.map((edge) => edge.to).find((id) => {
      const point = positions.get(id);
      return point !== undefined && point.y > fork.y;
    });
    const effectiveHeadId = firstOwnedId
      // An empty arm has no body head. Its entry successor may be the visible
      // condition call immediately before the hidden decision, so using it as
      // a continuation creates a feedback loop: shifting the "head" also
      // shifts the fork and every placement pass repeats the same movement.
      // Anchor empty controls to their real post-branch continuation instead.
      ?? (compactEmpty
        ? continuationHeadId
        // For IF, the entry successor is the selected branch body. For TRY it
        // is protected-body content upstream of catch dispatch.
        : group.kind === "TRY"
          ? continuationHeadId
          : group.entrySuccessorIds.find((id) => positions.has(id))
            ?? continuationHeadId);
    const head = effectiveHeadId ? positions.get(effectiveHeadId) : undefined;
    const headNode = effectiveHeadId ? nodeById.get(effectiveHeadId) : undefined;
    const minimumHeight = EMPTY_BRANCH_HEIGHT;
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
      ? fork.y + EMPTY_PANEL_NODE_GAP
      : head
        ? fork.y + (head.y - fork.y) / 2
        : fork.y + FILTERED_ROW_HEIGHT / 2;
    const controlsWidth = 78 + group.arms.reduce(
      (width, arm) => width
        + branchArmToggleWidth(arm)
        + 6,
      0,
    );
    return [{
      group,
      sourceIndex,
      forkY: fork.y,
      headId: effectiveHeadId,
      headY: head?.y,
      headClearance: (headNode ? nodeRadius(headNode) : 0) + EMPTY_PANEL_NODE_GAP,
      desiredY,
      x: Math.min(fork.x, minOwnedX) - BRANCH_REGION_PAD,
      controlsWidth,
      ownedNodeIds,
      compactEmpty,
      minimumHeight,
    }];
  });
}

function shiftDownstream(
  projection: VisibleGraphProjection,
  positions: Map<string, GraphPoint>,
  startIds: readonly string[],
  amount: number,
): void {
  if (amount <= 0 || startIds.length === 0) return;
  const outgoing = new Map<string, string[]>();
  for (const edge of projection.edges) {
    const targets = outgoing.get(edge.from);
    if (targets) targets.push(edge.to);
    else outgoing.set(edge.from, [edge.to]);
  }
  const downstream = new Set<string>();
  const pending = [...startIds];
  while (pending.length > 0) {
    const nodeId = pending.pop()!;
    if (downstream.has(nodeId)) continue;
    downstream.add(nodeId);
    pending.push(...(outgoing.get(nodeId) ?? []));
  }
  for (const nodeId of downstream) {
    const point = positions.get(nodeId);
    if (point) positions.set(nodeId, { ...point, y: point.y + amount });
  }
}

function placeBranchPanels(
  projection: VisibleGraphProjection,
  positions: Map<string, GraphPoint>,
  nodeById: ReadonlyMap<string, VisibleNode>,
): BranchGeometry[] {
  const encloses = (parent: VisibleBranchGroup, child: VisibleBranchGroup): boolean =>
    (child.enclosingRequirements ?? []).some((requirement) =>
      requirement.groupId === parent.id);
  const sortedCandidates = (): BranchCandidate[] =>
    buildBranchCandidates(projection, positions, nodeById).sort((left, right) =>
      left.group.instanceId.localeCompare(right.group.instanceId)
      // Parents must be placed before children so an invisible structural
      // decision can contribute a parent-relative virtual anchor.
      || (encloses(left.group, right.group) ? -1 : 0)
      || (encloses(right.group, left.group) ? 1 : 0)
      // A TRY's source line is the start of its protected body, but its
      // catch/noCatch split hangs from the body tail. Order every structure by
      // that actual fork position, exactly as we do for IF execution order.
      || left.forkY - right.forkY
      || (left.group.line ?? Number.MAX_SAFE_INTEGER) - (right.group.line ?? Number.MAX_SAFE_INTEGER)
      || left.sourceIndex - right.sourceIndex);
  let candidates = sortedCandidates();

  const explicitParentFor = (candidate: BranchCandidate): BranchCandidate | undefined =>
    [...(candidate.group.enclosingRequirements ?? [])]
      .reverse()
      .map((requirement) => candidates.find((possibleParent) =>
        possibleParent.group.id === requirement.groupId
        && possibleParent.group.selectedArmLabel === requirement.armLabel))
      .find((parent): parent is BranchCandidate => parent !== undefined);

  const laneFor = (candidate: BranchCandidate): string => {
    const explicitParent = explicitParentFor(candidate);
    if (explicitParent) {
      return `${candidate.group.instanceId}:${explicitParent.group.id}`;
    }
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
      return candidate.group.entryPredecessorIds.some((id) =>
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
    const placedYByGroup = new Map<string, number>();
    let shifted = false;
    for (const candidate of candidates) {
      const lane = laneFor(candidate);
      const parent = explicitParentFor(candidate);
      const parentY = parent ? placedYByGroup.get(parent.group.id) : undefined;
      // Hidden structural decisions must not collapse a nested panel onto its
      // parent's header. The enclosure contract supplies a virtual anchor:
      // place the child below the parent's controls even when projection
      // bridged both decisions to the same visible predecessor.
      const parentMinimumY = parent === undefined || parentY === undefined
        ? -Infinity
        : parentY + parent.minimumHeight + BRANCH_STACK_GAP;
      const minimumY = Math.max(
        nextYByLane.get(lane) ?? -Infinity,
        parentMinimumY,
      );
      const y = Math.max(candidate.desiredY, minimumY);
      placedYByGroup.set(candidate.group.id, y);
      if (
        candidate.compactEmpty
        && candidate.headY !== undefined
        && candidate.headY > candidate.forkY
        && y + candidate.minimumHeight + candidate.headClearance > candidate.headY
      ) {
        const overlap = y
          + candidate.minimumHeight
          + candidate.headClearance
          - candidate.headY;
        shiftDownstream(
          projection,
          positions,
          candidate.headId ? [candidate.headId] : [],
          overlap,
        );
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
        shiftDownstream(
          projection,
          positions,
          candidate.headId ? [candidate.headId] : [],
          2 * (minimumY - candidate.desiredY),
        );
        shifted = true;
        break;
      }
      const ownedBottom = [...candidate.ownedNodeIds].reduce((bottom, id) => {
        const point = positions.get(id);
        return point ? Math.max(bottom, point.y + BRANCH_REGION_PAD) : bottom;
      }, y + candidate.minimumHeight);
      const bottom = candidate.compactEmpty ? y + candidate.minimumHeight : ownedBottom;
      nextYByLane.set(lane, bottom + BRANCH_STACK_GAP);
    }
    if (!shifted) break;
    candidates = sortedCandidates();
  }

  const nextYByLane = new Map<string, number>();
  const nestingDepth = (candidate: BranchCandidate): number => {
    let depth = 0;
    let current: BranchCandidate | undefined = candidate;
    const visited = new Set<string>();
    while (current) {
      const parent = explicitParentFor(current);
      if (!parent || visited.has(parent.group.id)) break;
      visited.add(parent.group.id);
      depth++;
      current = parent;
    }
    return depth;
  };
  const geometryByCandidateId = new Map<string, BranchGeometry>();
  const geometries = candidates.map((candidate) => {
    const lane = laneFor(candidate);
    const parent = explicitParentFor(candidate);
    const parentGeometry = parent
      ? geometryByCandidateId.get(parent.group.id)
      : undefined;
    const parentMinimumY = parentGeometry
      ? parentGeometry.y + parent!.minimumHeight + BRANCH_STACK_GAP
      : -Infinity;
    const y = Math.max(
      candidate.desiredY,
      nextYByLane.get(lane) ?? -Infinity,
      parentMinimumY,
    );
    const ownedPoints = [...candidate.ownedNodeIds].flatMap((id) => {
      const point = positions.get(id);
      return point ? [point] : [];
    });
    const bottom = candidate.compactEmpty
      ? y + candidate.minimumHeight
      : Math.max(y + candidate.minimumHeight, ...ownedPoints.map((point) => point.y + BRANCH_REGION_PAD));
    const x = candidate.x + Math.min(nestingDepth(candidate) * 12, BRANCH_REGION_PAD - 4);
    let right = x + candidate.controlsWidth;
    for (const nodeId of candidate.ownedNodeIds) {
      const node = nodeById.get(nodeId);
      const point = positions.get(nodeId);
      if (!node || !point) continue;
      const labelWidth = nodeRadius(node) + 9
        + visibleNodeLabel(node).length * NODE_LABEL_CHAR_WIDTH;
      right = Math.max(right, point.x + labelWidth + BRANCH_REGION_PAD);
    }
    nextYByLane.set(lane, bottom + BRANCH_STACK_GAP);
    const geometry = {
      group: candidate.group,
      x,
      y,
      width: right - x,
      height: bottom - y,
      ownedNodeIds: candidate.ownedNodeIds,
      compactEmpty: candidate.compactEmpty,
    };
    geometryByCandidateId.set(candidate.group.id, geometry);
    return geometry;
  });

  // A parent's bounds include child panels even when the child owns no
  // visible nodes. Expand from the deepest panels upward without moving the
  // parent's header anchor.
  const geometryById = new Map(
    geometries.map((geometry) => [geometry.group.id, geometry]),
  );
  const candidatesByDepth = [...candidates]
    .sort((left, right) => nestingDepth(right) - nestingDepth(left));
  for (const childCandidate of candidatesByDepth) {
    const parentCandidate = explicitParentFor(childCandidate);
    if (!parentCandidate) continue;
    const child = geometryById.get(childCandidate.group.id);
    const parent = geometryById.get(parentCandidate.group.id);
    if (!child || !parent) continue;
    const right = Math.max(
      parent.x + parent.width,
      child.x + child.width + BRANCH_REGION_PAD / 2,
    );
    const bottom = Math.max(
      parent.y + parent.height,
      child.y + child.height + BRANCH_REGION_PAD / 2,
    );
    parent.width = right - parent.x;
    parent.height = bottom - parent.y;
  }
  return geometries;
}

export function layoutFilteredGraph(projection: VisibleGraphProjection): FilteredGraphLayout {
  const positions = layoutInstanceBlocks(projection);
  const nodeById = new Map(projection.nodes.map((node) => [node.id, node]));
  let branches = placeBranchPanels(projection, positions, nodeById);
  // The exit anchor owns downstream placement. Once nested child panels have
  // expanded their parents, reserve enough rows before the first visible
  // continuation and then rebuild the geometry against the shifted topology.
  const maxContinuationPasses = Math.max(2, branches.length * 2);
  for (let pass = 0; pass < maxContinuationPasses; pass++) {
    let shifted = false;
    for (const branch of [...branches].sort(
      (left, right) => left.y + left.height - (right.y + right.height),
    )) {
      // Compact panels already reserve their exact clearance from their head
      // during placement. Reprocessing their parent-expanded bounds here can
      // amplify a small empty-arm adjustment into a large vertical gap.
      if (branch.compactEmpty) continue;
      const continuationPoints = branch.group.continuationIds
        .map((id) => positions.get(id))
        .filter((point): point is GraphPoint => point !== undefined);
      if (continuationPoints.length === 0) continue;
      const continuationY = Math.min(...continuationPoints.map((point) => point.y));
      const requiredY = branch.y + branch.height + EMPTY_PANEL_NODE_GAP;
      if (continuationY >= requiredY) continue;
      shiftDownstream(
        projection,
        positions,
        branch.group.continuationIds,
        requiredY - continuationY,
      );
      shifted = true;
      break;
    }
    if (!shifted) break;
    branches = placeBranchPanels(projection, positions, nodeById);
  }
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
    const anchor = group.dispatchAnchorId ? positions.get(group.dispatchAnchorId) : undefined;
    if (!anchor) return [];
    return [anchor.x + DISPATCH_SELECTOR_WIDTH];
  });
  const nodeRightEdges = projection.nodes.flatMap((node) => {
    const point = positions.get(node.id);
    if (!point) return [];
    return [
      point.x + nodeRadius(node) + 15
        + visibleNodeLabel(node).length * NODE_LABEL_CHAR_WIDTH,
    ];
  });
  const width = Math.max(
    FILTERED_PAD_X * 2 + (maxDepth + 1) * FILTERED_COLUMN_WIDTH,
    ...nodeRightEdges.map((right) => right + FILTERED_PAD_X),
    ...branches.map((branch) => branch.x + branch.width + FILTERED_PAD_X),
    ...dispatchRightEdges.map((right) => right + FILTERED_PAD_X),
  );
  const height = Math.max(
    maxNodeY + FILTERED_PAD_Y + FILTERED_ROW_HEIGHT,
    ...branches.map((branch) => branch.y + branch.height + FILTERED_PAD_Y),
  );
  return { positions, branches, phases, width, height };
}
