import type { GraphBundle } from "../types/filteredGraph";
import type { LoopKind } from "../types/flowmap";
import type { VisibleNode } from "./filteredGraphProjection";

export interface VisibleLoopMembership {
  definitionLoopId: string;
  instanceLoopId: string;
  kind?: LoopKind;
  conditionCode?: string;
  line?: number;
  label: string;
}

/** Resolve definition loop ids without cloning loop topology per expansion. */
export function visibleNodeLoops(
  node: VisibleNode,
  bundle: GraphBundle,
): VisibleLoopMembership[] {
  const groups = new Map(
    (bundle.methodsByEntryId[node.methodEntryId]?.loopGroups ?? [])
      .map((group) => [group.id, group]),
  );
  return (node.node.loopIds ?? []).map((definitionLoopId) => {
    const group = groups.get(definitionLoopId);
    const kindLabel = group?.kind.toLowerCase().replaceAll("_", " ") ?? "loop";
    return {
      definitionLoopId,
      instanceLoopId: `${node.instanceId}:loop:${definitionLoopId}`,
      kind: group?.kind,
      conditionCode: group?.conditionCode,
      line: group?.line,
      label: group?.conditionCode ? `${kindLabel}: ${group.conditionCode}` : kindLabel,
    };
  });
}
