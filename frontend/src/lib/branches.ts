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
// What is left here is genuinely view logic: grouping, labelling, ordering,
// and selection state. Anything that needs to walk the graph belongs in
// cfg_pipeline.py, not here.
//
// Note the phase tree deliberately does NOT gate on polymorphism
// (DESIGN.md §6), so a dispatch panel's bounds will not line up with phase
// boxes.

import type { ArmTerminus, BranchGroup, FlowGraph, FlowNode } from "../types/flowmap";
import { indexNodesById, shortClassName, shortLabel } from "./graph";
import { computeWalkOrder } from "./layout";

export type BranchKind = "conditional" | "polymorphic";
export type PanelStructure = "IF" | "TRY" | "SWITCH" | "DISPATCH";

// Whether an arm is one of the mutually exclusive options the switcher
// offers, or something that is always on screen regardless.
//
//   alternative -- an IF arm, a catch arm, a dispatch target.
//   spine       -- a TRY's `try` arm. NOT an alternative: by the time the
//                  fork happens the try body has already run, which is why
//                  the switcher sits AFTER it and offers {none, catch1,
//                  ...} rather than treating `try` as one option among
//                  several (DESIGN.md, 2026-08-12).
//   always      -- `finally`. Runs on every path, so it belongs after the
//                  merge rather than inside the switcher.
export type ArmRole = "alternative" | "spine" | "always";

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
  // The backend's arm label: "if" / "elseif1" / "else" / "try" / "catch1"
  // / "impl1". Unique within its group, and the key its member nodes are
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
  // Which arm the switcher starts on. null for a TRY: "no exception
  // thrown" is a real state, and the correct default.
  defaultArmId: string | null;
  // IF/SWITCH/DISPATCH fork before their arms; a TRY's switcher belongs
  // after the try body, which has already run by the time it can throw.
  switcherPosition: "before" | "after";
}

export type BranchSelection = Map<string, string | null>;

function structureOf(group: BranchGroup): PanelStructure {
  if (group.kind === "TRY") return "TRY";
  if (group.kind === "SWITCH") return "SWITCH";
  if (group.kind === "DISPATCH") return "DISPATCH";
  return "IF";
}

function armRole(structure: PanelStructure, label: string): ArmRole {
  if (structure !== "TRY") return "alternative";
  if (label === "finally") return "always";
  return label === "try" ? "spine" : "alternative";
}

function armLabel(
  structure: PanelStructure,
  arm: { label: string; conditionCode?: string; firstCallId?: string },
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
    if (arm.label === "try" || arm.label === "finally") return arm.label;
    // The caught exception TYPE is not extracted -- full_cfg.sc records a
    // TRY arm's label and nothing else, and a catch arm has no
    // conditionCode by construction. Positional until it is.
    const n = arm.label.replace(/^catch/, "");
    return n ? `catch ${n}` : "catch";
  }
  return arm.label;
}

function armExit(
  terminus: ArmTerminus,
  group: BranchGroup,
): { exitTargetId?: string; exitKind: ArmExitKind } {
  if (terminus === "throw") return { exitKind: "throws" };
  if (terminus === "return") {
    const target = group.returnsTo?.[0];
    return target ? { exitTargetId: target, exitKind: "returns" } : { exitKind: "open" };
  }
  return group.convergesAt
    ? { exitTargetId: group.convergesAt, exitKind: "converges" }
    : { exitKind: "open" };
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
    const byArm = members.get(group.id) ?? new Map<string, string[]>();

    const arms: PanelArm[] = group.arms.map((arm) => {
      const memberIds = byArm.get(arm.label) ?? [];
      const terminus: ArmTerminus = arm.terminus ?? "continues";
      return {
        id: arm.label,
        label: armLabel(structure, arm, nodesById),
        role: armRole(structure, arm.label),
        conditionCode: arm.conditionCode,
        terminus,
        empty: arm.empty || memberIds.length === 0,
        headId: arm.firstCallId,
        memberIds,
        ...armExit(terminus, group),
      };
    });

    const alternatives = arms.filter((a) => a.role === "alternative");
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
      defaultArmId: structure === "TRY" ? null : (alternatives[0]?.id ?? null),
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

/**
 * Which nodes are on screen for a given selection.
 *
 * A node is hidden iff SOME panel owns it in an `alternative` arm that is
 * not the selected one. Membership is a list, not a lookup: a call inside
 * an `if` inside a `try` belongs to two panels at once, and it is only
 * visible when both agree -- so this is an intersection across panels, not
 * a per-panel decision. `spine` and `always` arms never hide anything (a
 * try body has already run by the time its catch can be chosen).
 */
export function visibleNodeIds(
  graph: FlowGraph,
  panels: BranchPanel[],
  selection: BranchSelection,
): Set<string> {
  const hidden = new Set<string>();
  for (const panel of panels) {
    const selected = selection.get(panel.id) ?? null;
    for (const arm of panel.arms) {
      if (arm.role !== "alternative" || arm.id === selected) continue;
      for (const id of arm.memberIds) hidden.add(id);
    }
  }

  const visible = new Set<string>();
  for (const node of graph.nodes) {
    if (!hidden.has(node.id)) visible.add(node.id);
  }
  return visible;
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
