interface SequenceEdge {
  from: string;
  to: string;
}

/**
 * Produce a stable topological order of a sequence CFG.
 *
 * Successor encounter order is only a tie-breaker: it is not an execution
 * order at a fork. In particular, a short branch can reach a shared join
 * before a longer sibling branch. Preorder DFS would then place the join (and
 * everything after it) before the longer branch. Condensing cycles first lets
 * us topologically order the resulting DAG while keeping loop members
 * contiguous and retaining deterministic backend encounter order.
 */
export function sequenceOrderedNodes<T extends { id: string }>(
  nodes: readonly T[],
  sequenceEdges: readonly SequenceEdge[],
  rootId: string | undefined,
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

  // First establish the stable encounter rank used to resolve otherwise
  // unordered siblings and disconnected components.
  const encounterOrder: string[] = [];
  const encountered = new Set<string>();
  const encounter = (nodeId: string): void => {
    if (encountered.has(nodeId) || !nodeById.has(nodeId)) return;
    encountered.add(nodeId);
    encounterOrder.push(nodeId);
    for (const targetId of successors.get(nodeId) ?? []) encounter(targetId);
  };
  if (rootId) encounter(rootId);
  for (const nodeId of [...nodeById.keys()].sort()) encounter(nodeId);
  const rank = new Map(encounterOrder.map((nodeId, index) => [nodeId, index]));

  // Tarjan SCC condensation makes loop-containing CFGs safe to topologically
  // order. Members of one loop component retain their encounter order.
  let nextIndex = 0;
  const indices = new Map<string, number>();
  const lowLinks = new Map<string, number>();
  const stack: string[] = [];
  const onStack = new Set<string>();
  const components: string[][] = [];
  const connect = (nodeId: string): void => {
    indices.set(nodeId, nextIndex);
    lowLinks.set(nodeId, nextIndex);
    nextIndex++;
    stack.push(nodeId);
    onStack.add(nodeId);

    for (const targetId of successors.get(nodeId) ?? []) {
      if (!indices.has(targetId)) {
        connect(targetId);
        lowLinks.set(nodeId, Math.min(lowLinks.get(nodeId)!, lowLinks.get(targetId)!));
      } else if (onStack.has(targetId)) {
        lowLinks.set(nodeId, Math.min(lowLinks.get(nodeId)!, indices.get(targetId)!));
      }
    }

    if (lowLinks.get(nodeId) !== indices.get(nodeId)) return;
    const component: string[] = [];
    while (stack.length > 0) {
      const member = stack.pop()!;
      onStack.delete(member);
      component.push(member);
      if (member === nodeId) break;
    }
    component.sort((left, right) => rank.get(left)! - rank.get(right)!);
    components.push(component);
  };
  for (const nodeId of encounterOrder) {
    if (!indices.has(nodeId)) connect(nodeId);
  }

  const componentByNode = new Map<string, number>();
  components.forEach((component, componentId) => {
    for (const nodeId of component) componentByNode.set(nodeId, componentId);
  });
  const componentSuccessors = new Map<number, Set<number>>();
  const indegree = new Array<number>(components.length).fill(0);
  for (const [sourceId, targets] of successors) {
    const sourceComponent = componentByNode.get(sourceId)!;
    for (const targetId of targets) {
      const targetComponent = componentByNode.get(targetId)!;
      if (sourceComponent === targetComponent) continue;
      const outgoing = componentSuccessors.get(sourceComponent) ?? new Set<number>();
      if (!outgoing.has(targetComponent)) {
        outgoing.add(targetComponent);
        indegree[targetComponent]++;
      }
      componentSuccessors.set(sourceComponent, outgoing);
    }
  }

  const componentRank = components.map((component) => rank.get(component[0])!);
  const ready = components
    .map((_, componentId) => componentId)
    .filter((componentId) => indegree[componentId] === 0);
  const ordered: T[] = [];
  while (ready.length > 0) {
    ready.sort((left, right) => componentRank[left] - componentRank[right]);
    const componentId = ready.shift()!;
    for (const nodeId of components[componentId]) ordered.push(nodeById.get(nodeId)!);
    for (const targetComponent of componentSuccessors.get(componentId) ?? []) {
      indegree[targetComponent]--;
      if (indegree[targetComponent] === 0) ready.push(targetComponent);
    }
  }
  return ordered;
}
