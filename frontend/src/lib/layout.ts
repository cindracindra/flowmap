import type { FlowGraph, FlowNode } from "../types/flowmap";

// Mirrors the backend's own visualization convention (DESIGN.md §1:
// "depth x walk-order"): depth is the invoke-nesting level of a node
// (crossing into a callee is what deepens the column), walk order is a
// traversal-visit counter used for vertical placement within a column.

const COLUMN_WIDTH = 190;
const ROW_HEIGHT = 68;
const PAD_X = 110;
const PAD_Y = 60;

// Depth is stamped server-side onto each clone at the nesting level the
// flattener created it at (cfg_pipeline.py's `clone`). It is deliberately
// NOT recomputed here.
//
// A local BFS over a FLATTENED graph's own edges is wrong, and has been
// written twice: once in the old Python visualizer (DESIGN.md §8's "Sixth
// bug" and "Seventh bug") and once in this file. A fallback/returnFrom-
// tagged "sequence" edge means "the containing method just returned",
// which is not the 0-cost "stay at the same level" a real sequence edge
// is -- and no edge type can express it, since a tail-call chain three
// deep pops back to the ORIGINAL caller's depth, not one level per edge.
// Measured against a current pipeline run, the local BFS disagreed with
// the server on 7 of 19 nodes, including both documented cases
// (e.getMessage() 1 vs 0, to.deposit(amount) 2 vs 1).
//
// A graph without the field is therefore a stale graph, not a graph to
// guess about: say so loudly and lay it out flat rather than render
// something plausible and wrong.
let warnedMissingDepth = false;

function depthOf(node: FlowNode): number {
  if (node.depth === undefined) {
    if (!warnedMissingDepth) {
      warnedMissingDepth = true;
      console.error(
        `[flowmap] node "${node.id}" has no server-computed depth. This graph ` +
          `predates the field -- regenerate it (main.py) and copy the output ` +
          `into src/data/. Layout is degraded until then.`,
      );
    }
    return 0;
  }
  return node.depth;
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
  const order = computeWalkOrder(graph, rootId);

  const positions = new Map<string, NodePosition>();
  for (const node of graph.nodes) {
    const depth = depthOf(node);
    const rank = order.get(node.id) ?? 0;
    positions.set(node.id, {
      x: PAD_X + depth * COLUMN_WIDTH,
      y: PAD_Y + rank * ROW_HEIGHT,
    });
  }
  return positions;
}
