// Turns the backend's branch groups into the model the branch panel
// renders: one BranchPanel per switchable fork, each with its arms, the
// node region each arm owns, and where that region ends.
//
// This file used to DERIVE most of that from the graph -- walking invoke
// edges to find an arm's region, scanning invoke-edge multiplicity to
// discover polymorphic call sites, and bounding regions by node depth
// because `convergesAt` was too often null to bound them with. All of that
// is now computed once in the backend, where the clone tree is actually
// known, and this is a straight adapter over the result:
//
//   arm region      <- node.branchArms, propagated through invoke edges at
//                      flatten time (verified identical to the old walk on
//                      all 31 conditional arms before the walk was deleted)
//   dispatch groups <- BranchGroup with kind "DISPATCH"
//   convergence     <- BranchGroup.convergesAt, for every kind alike
//
// What is left here is view logic: grouping, labelling, ordering, selection
// state, and the cheap selection-dependent reachability walk. Structural
// graph facts and route targets still belong in cfg_pipeline.py.
//
// Note the phase tree deliberately does NOT gate on polymorphism
// (DESIGN.md §6), so a dispatch panel's bounds will not line up with phase
// boxes.

import type { ArmTerminus, BranchGroup, FlowEdge, FlowGraph, FlowNode } from "../types/flowmap";
import { indexNodesById, shortClassName, shortLabel } from "./graph";
import { computeWalkOrder } from "./layout";

export type BranchKind = "conditional" | "polymorphic";
export type PanelStructure = "IF" | "TRY" | "SWITCH" | "DISPATCH";

// BranchGroup arms are exactly the mutually exclusive options offered by
// the switcher. A TRY's body and finally are ordinary flow outside it.
export type ArmRole = "alternative";

// Where an arm's region stops, which is also what its arrow points at when
// the arm is empty and has no node of its own to draw.
//
//   converges -- rejoins the flow at the group's convergesAt.
//   returns   -- leaves the method; points at the enclosing instance's
//                continuation, which is NOT the same node as convergesAt
//                whenever there is code after the branch.
//   throws    -- never rejoins. No target; render a terminal stub.
//   open      -- nothing in the data says where this ends. Rare now that
//                convergesAt resolves for guard clauses too, but still
//                real for a branch that runs off the end of the trace.
export type ArmExitKind = "converges" | "returns" | "throws" | "open";

export interface PanelArm {
  // The backend's arm label: "if" / "elseif1" / "else" / "catch1" /
  // "noCatch" / "impl1". Unique within its group, and the key its member nodes are
  // tagged with.
  id: string;
  label: string;
  role: ArmRole;
  // IF arms only -- an else-if chain is one group carrying a different
  // condition per arm. Absent on `else`, on every TRY arm, and on a
  // dispatch arm (what selects that is the receiver's runtime type).
  conditionCode?: string;
  terminus: ArmTerminus;
  // No surviving call. Renders as a labelled arrow from the fork straight
  // to exitTargetId with no node in between, which is what keeps it
  // selectable and lets the label carry the meaning.
  empty: boolean;
  headId?: string;
  // Every node this arm owns, straight from the node tags.
  memberIds: string[];
  exitTargetId?: string;
  exitTargetIds: string[];
  exitKind: ArmExitKind;
}

export interface BranchPanel {
  id: string;
  kind: BranchKind;
  structure: PanelStructure;
  title: string;
  subtitle?: string;
  method?: string;
  line?: number;
  // Where the panel attaches. A LIST because a TRY forks once per try tail.
  //
  // Two SEQUENTIAL branches in one method legitimately share an anchor: an
  // IF's condition is <operator>.* and is stripped as noise, so both walk
  // back to the method entry. Not an ambiguity -- the groups keep distinct
  // ids and lines -- but it does mean the anchor cannot order sibling
  // panels. Order by `line`; buildBranchPanels does.
  branchPointIds: string[];
  convergesAt?: string;
  arms: PanelArm[];
  // Which arm the switcher starts on. TRY defaults to its empty noCatch arm;
  // other structures default to their first source arm.
  defaultArmId: string;
  // IF/SWITCH/DISPATCH fork before their arms; a TRY's switcher belongs
  // after the try body, which has already run by the time it can throw.
  switcherPosition: "before" | "after";
}

export type BranchSelection = Map<string, string>;

function structureOf(group: BranchGroup): PanelStructure {
  if (group.kind === "TRY") return "TRY";
  if (group.kind === "SWITCH") return "SWITCH";
  if (group.kind === "DISPATCH") return "DISPATCH";
  return "IF";
}

function armRole(): ArmRole {
  return "alternative";
}

function armLabel(
  structure: PanelStructure,
  arm: { label: string; conditionCode?: string; exceptionType?: string; firstCallId?: string },
  nodesById: Map<string, FlowNode>,
): string {
  if (structure === "DISPATCH") {
    // The implementing class. Recovered from the arm's head, which for a
    // dispatch arm is the callee's own entry node -- so the backend does
    // not have to repeat the implementation's name on every tag.
    const head = arm.firstCallId ? nodesById.get(arm.firstCallId) : undefined;
    return head?.calleeFullName ? shortClassName(head.calleeFullName) : arm.label;
  }
  if (arm.conditionCode) return arm.conditionCode;
  if (structure === "TRY") {
    if (arm.label === "noCatch") return "no exception";
    if (arm.exceptionType) return `catch ${arm.exceptionType}`;
    // Backward-compatible positional label for artifacts generated before
    // the extractor started emitting exceptionType.
    const n = arm.label.replace(/^catch/, "");
    return n ? `catch ${n}` : "catch";
  }
  return arm.label;
}

function armExit(
  arm: { terminus?: ArmTerminus; targetIds?: string[] },
  group: BranchGroup,
): { exitTargetId?: string; exitTargetIds: string[]; exitKind: ArmExitKind } {
  const terminus = arm.terminus ?? "continues";
  if (terminus === "throw") return { exitTargetIds: [], exitKind: "throws" };
  // Backward-compatible while previously generated artifacts remain on
  // disk: targetIds is authoritative when present; older payloads derive
  // the same value from the group-level fields.
  const targets = arm.targetIds ?? (
    terminus === "return"
      ? (group.returnsTo ?? [])
      : (group.convergesAt ? [group.convergesAt] : [])
  );
  const target = targets[0];
  if (terminus === "return") {
    return target
      ? { exitTargetId: target, exitTargetIds: targets, exitKind: "returns" }
      : { exitTargetIds: [], exitKind: "open" };
  }
  return target
    ? { exitTargetId: target, exitTargetIds: targets, exitKind: "converges" }
    : { exitTargetIds: [], exitKind: "open" };
}

function panelTitle(
  group: BranchGroup,
  structure: PanelStructure,
  armCount: number,
  nodesById: Map<string, FlowNode>,
): { title: string; subtitle?: string } {
  if (structure === "DISPATCH") {
    // The DECLARED method -- what the caller actually wrote. The arms carry
    // the runtime types, which is the whole distinction being drawn.
    const site = nodesById.get(group.branchPointIds?.[0] ?? "");
    return {
      title: site?.calleeFullName ? shortLabel(site.calleeFullName) : "dispatch",
      subtitle: `${armCount} implementations`,
    };
  }
  return {
    title: structure === "TRY" ? "try / catch" : "if / else",
    subtitle: group.method ? shortLabel(group.method) : undefined,
  };
}

/**
 * All panels, in trace order.
 *
 * A group is DROPPED when it has no branch point (nothing to attach to) or
 * when every arm is empty -- `FeePolicy.cap`'s `if (fee > ceiling) return
 * ceiling;` is the live example. Its condition text and terminus are both
 * known, so this is a deliberate decision to drop rather than an inability
 * to render.
 *
 * Sorted by where the fork sits in the walk, then by source line. The line
 * tie-break is load-bearing rather than cosmetic: two sequential branches
 * in one method share a branch point (both conditions were stripped as
 * noise), so walk order alone cannot separate them and they would render in
 * arbitrary order instead of back-to-back in source order.
 */
export function buildBranchPanels(graph: FlowGraph, rootId: string): BranchPanel[] {
  const nodesById = indexNodesById(graph.nodes);
  const order = computeWalkOrder(graph, rootId);

  // groupId -> armLabel -> member node ids, straight off the node tags.
  const members = new Map<string, Map<string, string[]>>();
  for (const node of graph.nodes) {
    for (const ref of node.branchArms ?? []) {
      let byArm = members.get(ref.groupId);
      if (!byArm) {
        byArm = new Map();
        members.set(ref.groupId, byArm);
      }
      const list = byArm.get(ref.armLabel);
      if (list) list.push(node.id);
      else byArm.set(ref.armLabel, [node.id]);
    }
  }

  const panels: BranchPanel[] = [];
  for (const group of graph.branchGroups ?? []) {
    if (!group.branchPointIds?.length) continue;
    if (group.arms.every((arm) => arm.empty)) continue;

    const structure = structureOf(group);
    if (
      structure === "TRY" &&
      !group.arms.some((arm) => arm.label !== "noCatch" && !arm.empty)
    ) continue;
    const byArm = members.get(group.id) ?? new Map<string, string[]>();

    const arms: PanelArm[] = group.arms.map((arm) => {
      const memberIds = byArm.get(arm.label) ?? [];
      const terminus: ArmTerminus = arm.terminus ?? "continues";
      return {
        id: arm.label,
        label: armLabel(structure, arm, nodesById),
        role: armRole(),
        conditionCode: arm.conditionCode,
        terminus,
        empty: arm.empty || memberIds.length === 0,
        headId: arm.firstCallId,
        memberIds,
        ...armExit(arm, group),
      };
    });

    const alternatives = arms.filter((a) => a.role === "alternative");
    const defaultAlternative = structure === "TRY"
      ? alternatives.find((candidate) => candidate.id === "noCatch")
      : alternatives[0];
    if (!defaultAlternative) continue;
    panels.push({
      id: group.id,
      kind: structure === "DISPATCH" ? "polymorphic" : "conditional",
      structure,
      ...panelTitle(group, structure, alternatives.length, nodesById),
      method: group.method,
      line: group.line,
      branchPointIds: group.branchPointIds,
      convergesAt: group.convergesAt,
      arms,
      defaultArmId: defaultAlternative.id,
      switcherPosition: structure === "TRY" ? "after" : "before",
    });
  }

  const rank = (panel: BranchPanel) =>
    Math.min(...panel.branchPointIds.map((id) => order.get(id) ?? Number.MAX_SAFE_INTEGER));

  return panels.sort(
    (a, b) =>
      rank(a) - rank(b) ||
      (a.line ?? 0) - (b.line ?? 0) ||
      a.id.localeCompare(b.id),
  );
}

export function defaultSelection(panels: BranchPanel[]): BranchSelection {
  return new Map(panels.map((p) => [p.id, p.defaultArmId]));
}

/** Panels whose enclosing branch arms are currently selected.
 *
 * Flattening propagates an enclosing arm's membership into every nested
 * call. That makes containment explicit even when filtering removed both
 * conditions and left parent and child attached to the same anchor.
 */
export function activeBranchPanels(
  panels: BranchPanel[],
  selection: BranchSelection,
): BranchPanel[] {
  const members = new Map(
    panels.map((panel) => [
      panel.id,
      new Set(panel.arms.flatMap((arm) => arm.memberIds)),
    ]),
  );

  return panels.filter((child) => {
    const childMembers = members.get(child.id)!;
    for (const parent of panels) {
      if (parent.id === child.id) continue;
      for (const arm of parent.arms) {
        const parentMembers = new Set(arm.memberIds);
        const containsFork = child.branchPointIds.some((id) => parentMembers.has(id));
        const containsAllMembers = childMembers.size > 0
          && parentMembers.size >= childMembers.size
          && [...childMembers].every((id) => parentMembers.has(id));
        const equalSetInSameMethod = containsAllMembers
          && parentMembers.size === childMembers.size
          && parent.method === child.method
          && (parent.line ?? Number.MAX_SAFE_INTEGER) < (child.line ?? 0);
        const encloses = containsFork
          || (containsAllMembers && (parentMembers.size > childMembers.size || equalSetInSameMethod));
        if (!encloses) continue;

        const selected = selection.get(parent.id) ?? parent.defaultArmId;
        if (selected !== arm.id) return false;
      }
    }
    return true;
  });
}

/** Visible route candidates for the selected arm, including the next
 * sequential group when stripped conditions made both groups share an
 * anchor. New backend artifacts carry these in targetIds; the inferred
 * candidate keeps older saved traces visually correct.
 */
export function panelRouteTargetIds(
  panel: BranchPanel,
  panels: BranchPanel[],
  selection: BranchSelection,
): string[] {
  const selectedId = selection.get(panel.id) ?? panel.defaultArmId;
  const arm = panel.arms.find((candidate) => candidate.id === selectedId);
  if (!arm) return [];
  if (!arm.empty && arm.headId) return [arm.headId];

  const laterHeads = panels
    .filter((candidate) =>
      candidate.id !== panel.id
      && candidate.method === panel.method
      && (candidate.line ?? 0) > (panel.line ?? 0)
      && candidate.branchPointIds.some((id) => panel.branchPointIds.includes(id)),
    )
    .sort((a, b) => (a.line ?? 0) - (b.line ?? 0))
    .flatMap((candidate) => {
      const laterSelected = selection.get(candidate.id) ?? candidate.defaultArmId;
      const laterArm = candidate.arms.find((armCandidate) => armCandidate.id === laterSelected);
      return laterArm?.headId ? [laterArm.headId] : [];
    });
  const declared = arm.exitTargetIds.length > 0
    ? arm.exitTargetIds
    : (panel.convergesAt ? [panel.convergesAt] : []);
  return [...new Set([...laterHeads, ...declared])];
}

export function flowEdgeKey(edge: FlowEdge): string {
  return [
    edge.from,
    edge.to,
    edge.type,
    edge.returnFrom ?? "",
    edge.fallback ? "fallback" : "",
  ].join("|");
}

export interface VisibleGraphSelection {
  nodeIds: Set<string>;
  edgeIds: Set<string>;
}

/** Which nodes and edges are executable under the complete branch selection.
 *
 * Membership first removes unselected alternative bodies. A forward walk
 * from the graph root then gates the route edges at each unambiguous branch
 * point: a non-empty arm enters at its head, while an empty arm enters at
 * its resolved exit target. Common post-convergence flow is ordinary,
 * ungated flow and is discovered naturally by the walk.
 *
 * Day 1 deliberately does not infer ordering for consecutive groups whose
 * stripped conditions made them share one branch point. Those groups retain
 * the old membership-only behaviour until logical gates are preserved by
 * the backend; guessing here would hide valid flow.
 */
export function visibleGraphSelection(
  graph: FlowGraph,
  panels: BranchPanel[],
  selection: BranchSelection,
): VisibleGraphSelection {
  const hidden = new Set<string>();
  for (const panel of panels) {
    const selected = selection.get(panel.id) ?? panel.defaultArmId;
    for (const arm of panel.arms) {
      if (arm.role !== "alternative" || arm.id === selected) continue;
      for (const id of arm.memberIds) hidden.add(id);
    }
  }

  if (!graph.rootId || hidden.has(graph.rootId)) {
    return { nodeIds: new Set(), edgeIds: new Set() };
  }

  const groupsAtPoint = new Map<string, BranchPanel[]>();
  for (const panel of panels) {
    for (const point of panel.branchPointIds) {
      const groups = groupsAtPoint.get(point);
      if (groups) groups.push(panel);
      else groupsAtPoint.set(point, [panel]);
    }
  }

  const ambiguousPanelIds = new Set<string>();
  for (const groups of groupsAtPoint.values()) {
    if (groups.length > 1) {
      for (const panel of groups) ambiguousPanelIds.add(panel.id);
    }
  }

  const routeTargets = (panel: BranchPanel, selectedArmId: string): Set<string> => {
    const arm = panel.arms.find((candidate) => candidate.id === selectedArmId);
    if (!arm) return new Set();
    if (!arm.empty && arm.headId) return new Set([arm.headId]);
    return new Set(arm.exitTargetIds);
  };

  const routingPanels = panels.filter((panel) => !ambiguousPanelIds.has(panel.id));
  const selectedTargets = new Map(
    routingPanels.map((panel) => [
      panel.id,
      routeTargets(panel, selection.get(panel.id) ?? panel.defaultArmId),
    ]),
  );
  const allTargets = new Map(
    routingPanels.map((panel) => [
      panel.id,
      new Set(panel.arms.flatMap((arm) => [
        ...(arm.headId ? [arm.headId] : []),
        ...arm.exitTargetIds,
      ]).concat(panel.convergesAt ? [panel.convergesAt] : [])),
    ]),
  );

  const flowEdges = graph.edges.filter((edge) => edge.type !== "data" && !edge.loopBack);
  const hasAuthoritativeRequirements = flowEdges.some(
    (edge) => edge.branchRequirements !== undefined,
  );
  const outgoing = new Map<string, FlowEdge[]>();
  for (const edge of flowEdges) {
    const edges = outgoing.get(edge.from);
    if (edges) edges.push(edge);
    else outgoing.set(edge.from, [edge]);
  }

  const edgeAllowed = (edge: FlowEdge): boolean => {
    // A selected throw is unambiguous even when several stripped conditions
    // share one anchor: only its own arm head may be entered. This closes the
    // route immediately. Keep this invariant even for older authoritative
    // artifacts that omitted the earlier guard's normal requirement from a
    // later sibling's head.
    for (const panel of panels) {
      const selectedId = selection.get(panel.id) ?? panel.defaultArmId;
      const selectedArm = panel.arms.find((arm) => arm.id === selectedId);
      if (selectedArm?.terminus !== "throw") continue;
      const atDirectPoint = panel.branchPointIds.includes(edge.from);
      const atReturnedPoint = edge.returnFrom != null && panel.branchPointIds.includes(edge.returnFrom);
      if (!atDirectPoint && !atReturnedPoint) continue;
      if (panel.structure === "DISPATCH" ? edge.type !== "invoke" : edge.type !== "sequence") {
        continue;
      }
      const entersEarlierSibling = panels.some((candidate) =>
        candidate.id !== panel.id
        && candidate.method === panel.method
        && (candidate.line ?? 0) < (panel.line ?? 0)
        && candidate.branchPointIds.some((id) => panel.branchPointIds.includes(id))
        && candidate.arms.some((arm) => arm.headId === edge.to),
      );
      if (entersEarlierSibling) continue;
      if (edge.to !== selectedArm.headId) return false;
    }

    if (hasAuthoritativeRequirements) {
      return (edge.branchRequirements ?? []).every((requirement) => {
        // A backend group may intentionally have no switcher (for example,
        // every arm became empty after filtering). With no user selection
        // to apply, that requirement must not erase otherwise valid flow.
        if (!selection.has(requirement.groupId)) return true;
        return selection.get(requirement.groupId) === requirement.armLabel;
      });
    }

    // Backward compatibility for flattened artifacts generated before
    // branchRequirements became backend-authoritative.

    for (const panel of routingPanels) {
      const atDirectPoint = panel.branchPointIds.includes(edge.from);
      const atReturnedPoint = edge.returnFrom != null && panel.branchPointIds.includes(edge.returnFrom);
      if (!atDirectPoint && !atReturnedPoint) continue;

      // An IF/TRY route is a sequence edge; an invoke at its branch-point
      // call must still run before its return edge chooses the arm. DISPATCH
      // is the inverse: its alternatives are the invoke targets themselves.
      if (panel.structure === "DISPATCH" ? edge.type !== "invoke" : edge.type !== "sequence") {
        continue;
      }

      const candidates = allTargets.get(panel.id)!;
      if (!candidates.has(edge.to)) continue;
      if (!selectedTargets.get(panel.id)!.has(edge.to)) return false;
    }
    return true;
  };

  const visible = new Set<string>();
  const visibleEdges = new Set<string>();
  const pending = [graph.rootId];
  while (pending.length > 0) {
    const nodeId = pending.pop()!;
    if (visible.has(nodeId) || hidden.has(nodeId)) continue;
    visible.add(nodeId);
    for (const edge of outgoing.get(nodeId) ?? []) {
      if (hidden.has(edge.to) || !edgeAllowed(edge)) continue;
      visibleEdges.add(flowEdgeKey(edge));
      pending.push(edge.to);
    }
  }
  return { nodeIds: visible, edgeIds: visibleEdges };
}

/**
 * The panels a node belongs to, for the detail panel ("this call only runs
 * when amount > 1000" / "this is one of two implementations").
 */
export function panelsForNode(
  nodeId: string,
  panels: BranchPanel[],
): { panel: BranchPanel; arm: PanelArm }[] {
  const found: { panel: BranchPanel; arm: PanelArm }[] = [];
  for (const panel of panels) {
    for (const arm of panel.arms) {
      if (arm.memberIds.includes(nodeId)) found.push({ panel, arm });
    }
  }
  return found;
}
