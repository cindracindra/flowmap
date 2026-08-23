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
): { exitTargetId?: string; exitTargetIds: string[]; exitKind: ArmExitKind } {
  const terminus = arm.terminus ?? "continues";
  if (terminus === "throw") return { exitTargetIds: [], exitKind: "throws" };
  const targets = arm.targetIds ?? [];
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
    // A zero-call arm can still change control flow.  In particular,
    // `if (...) return;` has two visually empty arms but selecting one must
    // leave the method while the other continues to the next call.
    if (
      group.arms.every((arm) => arm.empty) &&
      group.arms.every((arm) => (arm.terminus ?? "continues") === "continues")
    ) continue;

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
        ...armExit(arm),
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

/** Backend-declared route targets for the selected arm. */
export function panelRouteTargetIds(
  panel: BranchPanel,
  selection: BranchSelection,
): string[] {
  const selectedId = selection.get(panel.id) ?? panel.defaultArmId;
  const arm = panel.arms.find((candidate) => candidate.id === selectedId);
  if (!arm) return [];
  if (!arm.empty && arm.headId) return [arm.headId];
  return arm.exitTargetIds;
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

/** Which nodes and edges execute under the complete branch selection.
 *
 * The backend's edge requirements are the sole execution contract. Node arm
 * membership remains presentation metadata for panels and highlighting; it
 * must not independently hide flow. A root-forward walk makes every node
 * reachable through allowed edges visible, including common flow after
 * convergence.
 */
export function visibleGraphSelection(
  graph: FlowGraph,
  selection: BranchSelection,
): VisibleGraphSelection {
  if (!graph.rootId) {
    return { nodeIds: new Set(), edgeIds: new Set() };
  }

  const flowEdges = graph.edges.filter((edge) => edge.type !== "data" && !edge.loopBack);
  const outgoing = new Map<string, FlowEdge[]>();
  for (const edge of flowEdges) {
    const edges = outgoing.get(edge.from);
    if (edges) edges.push(edge);
    else outgoing.set(edge.from, [edge]);
  }

  const edgeAllowed = (edge: FlowEdge): boolean =>
    (edge.branchRequirements ?? []).every((requirement) => {
      // Groups omitted from the panel have no user-controlled selection.
      // Their metadata must not erase otherwise executable flow.
      if (!selection.has(requirement.groupId)) return true;
      return selection.get(requirement.groupId) === requirement.armLabel;
    });

  const visible = new Set<string>();
  const visibleEdges = new Set<string>();
  const pending = [graph.rootId];
  while (pending.length > 0) {
    const nodeId = pending.pop()!;
    if (visible.has(nodeId)) continue;
    visible.add(nodeId);
    for (const edge of outgoing.get(nodeId) ?? []) {
      if (!edgeAllowed(edge)) continue;
      visibleEdges.add(flowEdgeKey(edge));
      pending.push(edge.to);
    }
  }
  return { nodeIds: visible, edgeIds: visibleEdges };
}

/**
 * Find a root-to-node route and return the branch choices required to make
 * that route visible. Edge requirements are authoritative; node arm tags
 * describe containment and are not sufficient for common flow after a
 * branch or for nodes reached through synthesized return edges.
 */
export function branchSelectionForNode(
  graph: FlowGraph,
  nodeId: string,
  current: BranchSelection,
  panels: BranchPanel[],
): BranchSelection | null {
  if (!graph.rootId) return null;
  const controlledGroups = new Set(panels.map((panel) => panel.id));
  const outgoing = new Map<string, FlowEdge[]>();
  for (const edge of graph.edges) {
    if (edge.type === "data" || edge.loopBack) continue;
    const edges = outgoing.get(edge.from);
    if (edges) edges.push(edge);
    else outgoing.set(edge.from, [edge]);
  }

  interface SearchState {
    id: string;
    required: Map<string, string>;
  }
  const keyOf = (state: SearchState) => `${state.id}|${[...state.required]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([group, arm]) => `${group}=${arm}`)
    .join(";")}`;
  const queue: SearchState[] = [{ id: graph.rootId, required: new Map() }];
  const visited = new Set<string>();

  for (let head = 0; head < queue.length; head++) {
    const state = queue[head];
    const stateKey = keyOf(state);
    if (visited.has(stateKey)) continue;
    visited.add(stateKey);
    if (state.id === nodeId) {
      const selection = new Map(current);
      for (const [group, arm] of state.required) selection.set(group, arm);
      return selection;
    }

    for (const edge of outgoing.get(state.id) ?? []) {
      const required = new Map(state.required);
      let conflict = false;
      for (const requirement of edge.branchRequirements ?? []) {
        if (!controlledGroups.has(requirement.groupId)) continue;
        const existing = required.get(requirement.groupId);
        if (existing !== undefined && existing !== requirement.armLabel) {
          conflict = true;
          break;
        }
        required.set(requirement.groupId, requirement.armLabel);
      }
      if (!conflict) queue.push({ id: edge.to, required });
    }
  }
  return null;
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
