interface SequenceEdge {
  from: string;
  to: string;
}

/**
 * Preserve CFG reachability while making DFS successor ties deterministic.
 * Earlier source locations win; the backend edge order remains the stable
 * fallback for nodes on the same line or without usable location metadata.
 */
function sourceOrderedSuccessors<T>(
  targetIds: readonly string[],
  nodeById: ReadonlyMap<string, T>,
  sourceLineOf: (node: T) => number | undefined,
): string[] {
  const sourceLine = (id: string): number => {
    const node = nodeById.get(id);
    const line = node === undefined ? undefined : sourceLineOf(node);
    return line !== undefined && line >= 0 ? line : Number.MAX_SAFE_INTEGER;
  };

  return targetIds
    .map((id, index) => ({ id, index, line: sourceLine(id) }))
    .sort((left, right) => left.line - right.line || left.index - right.index)
    .map(({ id }) => id);
}

/**
 * Walk a sequence CFG once from its root. CFG edges determine reachability;
 * source line only breaks a tie between multiple successors. A visited set
 * makes cycles safe, and disconnected nodes remain inspectable afterward.
 */
export function sequenceOrderedNodes<T extends { id: string }>(
  nodes: readonly T[],
  sequenceEdges: readonly SequenceEdge[],
  rootId: string | undefined,
  sourceLineOf: (node: T) => number | undefined,
): T[] {
  const nodeById = new Map(nodes.map((node) => [node.id, node] as const));
  const successors = new Map<string, string[]>();
  for (const edge of sequenceEdges) {
    if (!nodeById.has(edge.from) || !nodeById.has(edge.to)) continue;
    const targets = successors.get(edge.from);
    if (targets) {
      if (!targets.includes(edge.to)) targets.push(edge.to);
    } else {
      successors.set(edge.from, [edge.to]);
    }
  }

  const ordered: T[] = [];
  const visited = new Set<string>();
  const visit = (nodeId: string): void => {
    if (visited.has(nodeId)) return;
    const node = nodeById.get(nodeId);
    if (!node) return;
    visited.add(nodeId);
    ordered.push(node);
    for (const targetId of sourceOrderedSuccessors(
      successors.get(nodeId) ?? [],
      nodeById,
      sourceLineOf,
    )) {
      visit(targetId);
    }
  };

  if (rootId) visit(rootId);
  for (const nodeId of [...nodeById.keys()].sort()) visit(nodeId);
  return ordered;
}
