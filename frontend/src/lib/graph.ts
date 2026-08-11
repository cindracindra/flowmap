import type { FlowNode, FlowEdge, Phase, TransitionReason } from "../types/flowmap";
import type { NodePosition } from "./layout";

function splitMethodFullName(fullName: string): { className: string; method: string } {
  const [pathPart] = fullName.split(":");
  const segments = pathPart.split(".");
  const method = segments.pop() ?? pathPart;
  return { className: segments.join("."), method };
}

function splitClassName(classFullName: string): { pkg: string; shortName: string } {
  const segments = classFullName.split(".");
  const shortName = segments.pop() ?? classFullName;
  return { pkg: segments.join(".") || "(default package)", shortName };
}

export function classOf(methodFullName: string): string {
  return splitMethodFullName(methodFullName).className;
}

// "Class.method()" display form, or "new Class()" for a constructor.
export function shortLabel(methodFullName: string): string {
  const { className, method } = splitMethodFullName(methodFullName);
  const { shortName } = splitClassName(className);
  return method === "<init>" ? `new ${shortName}()` : `${shortName}.${method}()`;
}

// The class a node is best understood as belonging to: for a call site,
// that's the class it's written inside (callerMethod); for an entry/leaf,
// it's the method's own class.
export function ownerClassOf(node: FlowNode): string {
  const methodFullName = node.callerMethod ?? node.calleeFullName;
  return methodFullName ? classOf(methodFullName) : "(unknown)";
}

export function indexNodesById(nodes: FlowNode[]): Map<string, FlowNode> {
  return new Map(nodes.map((n) => [n.id, n]));
}

export interface ExplorerItem {
  name: string;
  kind: "package" | "class" | "node";
  nodeId?: string;
  children?: ExplorerItem[];
}

// Groups nodes by package -> owning class, replacing the mock's hand-authored
// FILE_TREE with the real package/class structure derived from the trace.
export function buildExplorerTree(nodes: FlowNode[]): ExplorerItem[] {
  const byPackage = new Map<string, Map<string, FlowNode[]>>();
  for (const node of nodes) {
    if (!node.calleeFullName && !node.callerMethod) continue;
    const { pkg, shortName } = splitClassName(ownerClassOf(node));
    if (!byPackage.has(pkg)) byPackage.set(pkg, new Map());
    const classes = byPackage.get(pkg)!;
    if (!classes.has(shortName)) classes.set(shortName, []);
    classes.get(shortName)!.push(node);
  }

  const packages: ExplorerItem[] = [];
  for (const [pkg, classes] of [...byPackage.entries()].sort(([a], [b]) => a.localeCompare(b))) {
    const classItems: ExplorerItem[] = [];
    for (const [cls, classNodes] of [...classes.entries()].sort(([a], [b]) => a.localeCompare(b))) {
      const sorted = [...classNodes].sort((a, b) => (a.line ?? 0) - (b.line ?? 0));
      classItems.push({
        name: cls,
        kind: "class",
        children: sorted.map((n) => ({
          name: n.code ?? (n.calleeFullName ? shortLabel(n.calleeFullName) : n.id),
          kind: "node",
          nodeId: n.id,
        })),
      });
    }
    packages.push({ name: pkg, kind: "package", children: classItems });
  }
  return packages;
}

export function phaseIndexForNode(nodeId: string, phases: Phase[]): number | null {
  const idx = phases.findIndex((p) => p.nodes.includes(nodeId));
  return idx === -1 ? null : idx;
}

export function incomingEdges(nodeId: string, edges: FlowEdge[]): FlowEdge[] {
  return edges.filter((e) => e.to === nodeId);
}

export function outgoingEdges(nodeId: string, edges: FlowEdge[]): FlowEdge[] {
  return edges.filter((e) => e.from === nodeId);
}

export interface PhaseBBox {
  x: number;
  y: number;
  width: number;
  height: number;
}

export function computePhaseBBox(
  phase: Phase,
  positions: Map<string, NodePosition>,
  padding = 34,
): PhaseBBox | null {
  const points = phase.nodes.map((id) => positions.get(id)).filter((p): p is NodePosition => !!p);
  if (points.length === 0) return null;
  const xs = points.map((p) => p.x);
  const ys = points.map((p) => p.y);
  const minX = Math.min(...xs) - padding;
  const maxX = Math.max(...xs) + padding;
  const minY = Math.min(...ys) - padding;
  const maxY = Math.max(...ys) + padding;
  return { x: minX, y: minY, width: maxX - minX, height: maxY - minY };
}

// Plain-English gloss for each PHASING_RULES.md transition reason, shown in
// the detail panel so the cascade's output is legible without reading the doc.
export const TRANSITION_REASON_LABELS: Record<TransitionReason, string> = {
  gate: "Real branch or merge point in the code (an if/else fork or reconvergence)",
  "data-related": "Shares a direct data-flow edge with the previous step",
  "same-callee-related": "Same method called again with different arguments — treated as one step",
  "data-unrelated": "No data relationship found with the previous step",
  "class-related": "Same receiver class as the previous step",
  "class-unrelated": "Different receiver class, no other relation found",
  "dead-end": "This branch terminates (e.g. a thrown exception) and merges unconditionally into its origin",
};
