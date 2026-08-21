import type { FlowGraph, FlowNode } from "../types/flowmap";
import type { BranchPanel, PanelArm } from "./branches";
import { computeWalkOrder } from "./layout";

/**
 * Structural model consumed by the future global-row layout.
 *
 * The flattened CFG remains the execution authority. This tree is a view
 * model: it restores method and branch containment without inventing edges
 * or changing which nodes execute.
 */
export interface DisplayHierarchy {
  roots: DisplayMethod[];
  methodsByEntryId: Map<string, DisplayMethod>;
  branchesById: Map<string, DisplayBranch>;
  operationsById: Map<string, DisplayOperation>;
}

export interface SerializedDisplayHierarchy {
  roots: SerializedDisplayMethod[];
}

export type SerializedDisplayItem =
  | SerializedDisplayMethod
  | SerializedDisplayBranch
  | SerializedDisplayOperation;

export interface SerializedDisplayMethod {
  kind: "method";
  entryId: string;
  items: SerializedDisplayItem[];
}

export interface SerializedDisplayBranch {
  kind: "branch";
  panelId: string;
  arms: SerializedDisplayArm[];
}

export interface SerializedDisplayArm {
  kind: "arm";
  panelId: string;
  armId: string;
  items: SerializedDisplayItem[];
}

export interface SerializedDisplayOperation {
  kind: "operation";
  nodeId: string;
}

export type DisplayItem = DisplayMethod | DisplayBranch | DisplayOperation;

export interface DisplayMethod {
  kind: "method";
  entryId: string;
  methodFullName?: string;
  depth: number;
  items: DisplayItem[];
}

export interface DisplayBranch {
  kind: "branch";
  panelId: string;
  panel: BranchPanel;
  arms: DisplayArm[];
}

export interface DisplayArm {
  kind: "arm";
  panelId: string;
  armId: string;
  arm: PanelArm;
  items: DisplayItem[];
}

export interface DisplayOperation {
  kind: "operation";
  nodeId: string;
  node: FlowNode;
}

export interface DisplayHeader {
  kind: "method";
  id: string;
  label: string;
}

interface MethodRecord {
  model: DisplayMethod;
  parent?: MethodRecord;
  order: number;
}

type ItemOwner = DisplayMethod | DisplayArm;

function itemsOf(owner: ItemOwner): DisplayItem[] {
  return owner.items;
}

/** Restore nested display containment client-side for pre-sidecar artifacts. */
export function deriveDisplayHierarchy(
  graph: FlowGraph,
  rootId: string,
  panels: BranchPanel[],
): DisplayHierarchy {
  const order = computeWalkOrder(graph, rootId);
  const orderedNodes = [...graph.nodes].sort(
    (a, b) => (order.get(a.id) ?? Number.MAX_SAFE_INTEGER) -
      (order.get(b.id) ?? Number.MAX_SAFE_INTEGER),
  );

  // A flattened clone's server-provided depth is its method-instance stack
  // depth. Walking in stable CFG order lets an entry replace the active
  // scope at that depth, which distinguishes two clones of the same method.
  const activeAtDepth: Array<MethodRecord | undefined> = [];
  const methodForNode = new Map<string, MethodRecord>();
  const methodRecords: MethodRecord[] = [];
  for (const node of orderedNodes) {
    const depth = node.depth ?? 0;
    if (node.type === "entry") {
      const record: MethodRecord = {
        model: {
          kind: "method",
          entryId: node.id,
          methodFullName: node.calleeFullName,
          depth,
          items: [],
        },
        parent: depth > 0 ? activeAtDepth[depth - 1] : undefined,
        order: order.get(node.id) ?? Number.MAX_SAFE_INTEGER,
      };
      activeAtDepth.length = depth + 1;
      activeAtDepth[depth] = record;
      methodRecords.push(record);
      methodForNode.set(node.id, record);
      continue;
    }
    const owner = activeAtDepth[depth];
    if (!owner) continue;
    methodForNode.set(node.id, owner);
  }

  const branchesById = new Map<string, DisplayBranch>();
  const armsByKey = new Map<string, DisplayArm>();
  for (const panel of panels) {
    const branch: DisplayBranch = {
      kind: "branch",
      panelId: panel.id,
      panel,
      arms: panel.arms.map((arm) => ({
        kind: "arm",
        panelId: panel.id,
        armId: arm.id,
        arm,
        items: [],
      })),
    };
    branchesById.set(panel.id, branch);
    for (const arm of branch.arms) armsByKey.set(`${panel.id}\u0000${arm.armId}`, arm);
  }

  const nodeById = new Map(graph.nodes.map((node) => [node.id, node]));
  const methodForPanel = new Map(
    panels.flatMap((panel) => {
      const method = panel.branchPointIds
        .map((id) => methodForNode.get(id))
        .find((record): record is MethodRecord => record !== undefined);
      return method ? [[panel.id, method] as const] : [];
    }),
  );

  const armInMethodForNode = (
    node: FlowNode,
    method: MethodRecord | undefined,
  ): DisplayArm | undefined => {
    let best: { arm: DisplayArm; size: number } | undefined;
    for (const ref of node.branchArms ?? []) {
      if (methodForPanel.get(ref.groupId) !== method) continue;
      const arm = armsByKey.get(`${ref.groupId}\u0000${ref.armLabel}`);
      if (!arm) continue;
      const size = arm.arm.memberIds.length;
      if (!best || size < best.size) best = { arm, size };
    }
    return best?.arm;
  };

  // A nested branch belongs to the innermost arm containing its attachment
  // point. If its fork is not itself in an arm, it belongs to that fork's
  // method instance.
  for (const panel of panels) {
    const branch = branchesById.get(panel.id)!;
    const anchor = panel.branchPointIds
      .map((id) => nodeById.get(id))
      .find((node): node is FlowNode => node !== undefined);
    const owner: ItemOwner | undefined = anchor
      ? armInMethodForNode(anchor, methodForNode.get(anchor.id)) ?? methodForNode.get(anchor.id)?.model
      : undefined;
    if (owner) itemsOf(owner).push(branch);
  }

  const operationsById = new Map<string, DisplayOperation>();
  for (const node of orderedNodes) {
    if (node.type === "entry") continue;
    const operation: DisplayOperation = { kind: "operation", nodeId: node.id, node };
    operationsById.set(node.id, operation);
    const method = methodForNode.get(node.id);
    const owner = armInMethodForNode(node, method) ?? method?.model;
    if (owner) itemsOf(owner).push(operation);
  }

  // Entry nodes become method headers. Put each method under the innermost
  // arm carried by its entry, otherwise under its caller method.
  for (const record of methodRecords) {
    const entry = nodeById.get(record.model.entryId)!;
    const owner = armInMethodForNode(entry, record.parent) ?? record.parent?.model;
    if (owner) itemsOf(owner).push(record.model);
  }

  const itemOrder = (item: DisplayItem): number => {
    if (item.kind === "operation") return order.get(item.nodeId) ?? Number.MAX_SAFE_INTEGER;
    if (item.kind === "method") return order.get(item.entryId) ?? Number.MAX_SAFE_INTEGER;
    // The attachment operation occupies the row before its branch block.
    return Math.min(
      ...item.panel.branchPointIds.map((id) => order.get(id) ?? Number.MAX_SAFE_INTEGER),
    ) + 0.25;
  };
  const sortOwner = (owner: ItemOwner) => {
    owner.items.sort((a, b) => itemOrder(a) - itemOrder(b));
    for (const item of owner.items) {
      if (item.kind === "method") sortOwner(item);
      else if (item.kind === "branch") item.arms.forEach(sortOwner);
    }
  };

  const roots = methodRecords
    .filter((record) => !record.parent)
    .sort((a, b) => a.order - b.order)
    .map((record) => record.model);
  roots.forEach(sortOwner);

  return {
    roots,
    methodsByEntryId: new Map(methodRecords.map((record) => [record.model.entryId, record.model])),
    branchesById,
    operationsById,
  };
}

/** Resolve an ID-only backend sidecar against the existing graph maps. */
export function hydrateDisplayHierarchy(
  serialized: SerializedDisplayHierarchy,
  graph: FlowGraph,
  panels: BranchPanel[],
): DisplayHierarchy {
  const nodesById = new Map(graph.nodes.map((node) => [node.id, node]));
  const panelsById = new Map(panels.map((panel) => [panel.id, panel]));
  const methodsByEntryId = new Map<string, DisplayMethod>();
  const branchesById = new Map<string, DisplayBranch>();
  const operationsById = new Map<string, DisplayOperation>();

  const hydrateItems = (items: SerializedDisplayItem[]): DisplayItem[] =>
    items.flatMap<DisplayItem>((item): DisplayItem[] => {
      if (item.kind === "operation") {
        const node = nodesById.get(item.nodeId);
        if (!node) return [];
        const operation: DisplayOperation = { kind: "operation", nodeId: item.nodeId, node };
        operationsById.set(item.nodeId, operation);
        return [operation];
      }
      if (item.kind === "method") {
        const entry = nodesById.get(item.entryId);
        if (!entry) return [];
        const method: DisplayMethod = {
          kind: "method",
          entryId: item.entryId,
          methodFullName: entry.calleeFullName,
          depth: entry.depth ?? 0,
          items: hydrateItems(item.items),
        };
        methodsByEntryId.set(item.entryId, method);
        return [method];
      }
      const panel = panelsById.get(item.panelId);
      if (!panel) return [];
      const arms: DisplayArm[] = item.arms.flatMap((rawArm) => {
        const arm = panel.arms.find((candidate) => candidate.id === rawArm.armId);
        return arm ? [{
          kind: "arm" as const,
          panelId: item.panelId,
          armId: rawArm.armId,
          arm,
          items: hydrateItems(rawArm.items),
        }] : [];
      });
      const branch: DisplayBranch = { kind: "branch", panelId: item.panelId, panel, arms };
      branchesById.set(item.panelId, branch);
      return [branch];
    });

  const roots = serialized.roots.flatMap((root) =>
    hydrateItems([root]).filter((item): item is DisplayMethod => item.kind === "method")
  );
  return { roots, methodsByEntryId, branchesById, operationsById };
}

/** Header ancestry for every rendered graph node. */
export function buildDisplayHeaderPaths(
  hierarchy: DisplayHierarchy,
): Map<string, DisplayHeader[]> {
  const paths = new Map<string, DisplayHeader[]>();

  const visitItems = (items: DisplayItem[], path: DisplayHeader[]) => {
    for (const item of items) {
      if (item.kind === "operation") {
        paths.set(item.nodeId, path);
        continue;
      }
      if (item.kind === "method") {
        const next = [...path, {
          kind: "method" as const,
          id: item.entryId,
          label: item.methodFullName ?? item.entryId,
        }];
        paths.set(item.entryId, next);
        visitItems(item.items, next);
        continue;
      }
      for (const arm of item.arms) {
        visitItems(arm.items, path);
      }
    }
  };

  visitItems(hierarchy.roots, []);
  return paths;
}
