import type { FlowGraph } from "../types/flowmap";

// Mirrors the backend's own visualization convention (DESIGN.md §1:
// "depth x walk-order", visualizer.py's _compute_depths / _compute_sequence_order):
// depth is a 0-1 BFS where "sequence" edges cost 0 and "invoke" edges cost 1
// (crossing into a callee is what deepens the column), walk order is a
// traversal-visit counter used for vertical placement within a column.

const COLUMN_WIDTH = 190;
const ROW_HEIGHT = 68;
const PAD_X = 110;
const PAD_Y = 60;

// Recomputing depth by walking THIS graph's own edges is only correct on
// a graph with no fallback/returnFrom-tagged "sequence" edges -- crossing
// one of those means "this call's containing method just returned",
// which isn't a 0-cost "stay at the same level" the way a genuine
// same-method sequence edge is (DESIGN.md §8's "Sixth bug"/"Seventh
// bug", and the 2026-08-10 session-log entry, §0, for a concrete example:
// a node reached only via a fallback edge from a depth-1 entry rendered
// at depth 1 instead of its true depth 0). cfg_pipeline.py now computes
// depth server-side against the PRE-flatten graph (where this ambiguity
// doesn't exist) and stamps it onto each node via origId -- this local
// BFS is kept only as a fallback for graphs generated before that field
// existed, so an older flattened_cfg.json still renders instead of
// crashing.
function computeDepthsFromEdges(graph: FlowGraph, rootId: string): Map<string, number> {
  const adjacency = new Map<string, { to: string; cost: number }[]>();
  for (const edge of graph.edges) {
    if (edge.type !== "sequence" && edge.type !== "invoke") continue;
    const cost = edge.type === "invoke" ? 1 : 0;
    if (!adjacency.has(edge.from)) adjacency.set(edge.from, []);
    adjacency.get(edge.from)!.push({ to: edge.to, cost });
  }

  const depths = new Map<string, number>();
  depths.set(rootId, 0);
  const deque: string[] = [rootId];
  while (deque.length > 0) {
    const nodeId = deque.shift()!;
    const nodeDepth = depths.get(nodeId)!;
    for (const { to, cost } of adjacency.get(nodeId) ?? []) {
      const candidate = nodeDepth + cost;
      if (!depths.has(to) || candidate < depths.get(to)!) {
        depths.set(to, candidate);
        if (cost === 0) deque.unshift(to);
        else deque.push(to);
      }
    }
  }
  return depths;
}

export function computeDepths(graph: FlowGraph, rootId: string): Map<string, number> {
  if (graph.nodes.length > 0 && graph.nodes.every((n) => n.depth !== undefined)) {
    return new Map(graph.nodes.map((n) => [n.id, n.depth!]));
  }
  return computeDepthsFromEdges(graph, rootId);
}

export function computeWalkOrder(graph: FlowGraph, rootId: string): Map<string, number> {
  const adjacency = new Map<string, string[]>();
  for (const edge of graph.edges) {
    if (edge.type !== "sequence" && edge.type !== "invoke") continue;
    if (!adjacency.has(edge.from)) adjacency.set(edge.from, []);
    adjacency.get(edge.from)!.push(edge.to);
  }

  const order = new Map<string, number>();
  let counter = 0;
  const visit = (nodeId: string) => {
    if (order.has(nodeId)) return;
    order.set(nodeId, counter++);
    for (const next of adjacency.get(nodeId) ?? []) visit(next);
  };
  visit(rootId);

  // Any node unreachable from root (shouldn't happen for a rooted flattened
  // trace, but stay defensive) still gets a position instead of vanishing.
  for (const node of graph.nodes) {
    if (!order.has(node.id)) order.set(node.id, counter++);
  }
  return order;
}

export interface NodePosition {
  x: number;
  y: number;
}

export function computeLayout(graph: FlowGraph, rootId: string): Map<string, NodePosition> {
  const depths = computeDepths(graph, rootId);
  const order = computeWalkOrder(graph, rootId);

  const positions = new Map<string, NodePosition>();
  for (const node of graph.nodes) {
    const depth = depths.get(node.id) ?? 0;
    const rank = order.get(node.id) ?? 0;
    positions.set(node.id, {
      x: PAD_X + depth * COLUMN_WIDTH,
      y: PAD_Y + rank * ROW_HEIGHT,
    });
  }
  return positions;
}
