// Where a branch panel is drawn on the canvas.
//
// The region covers the branch's TERRITORY: everything between the fork and
// the convergence point, convergence EXCLUSIVE. The merge node is where the
// branch has stopped mattering -- both alternatives reach it -- so it sits
// outside the box, and the box's bottom edge is the visual statement of
// "past here, the choice no longer applies".

import { panelRouteTargetIds, type BranchPanel, type PanelArm } from "./branches";
import { computeBBox } from "./graph";
import type { FlowEdge } from "../types/flowmap";
import { ROW_HEIGHT, type NodePosition } from "./layout";

export interface PanelGeometry {
  panel: BranchPanel;
  // The arm currently on screen.
  arm: PanelArm;
  x: number;
  y: number;
  width: number;
  height: number;
  // Fork and merge, when both are on screen. The switcher hangs off the
  // fork; the merge gets a marker drawn OUTSIDE the region.
  fork?: NodePosition;
  merge?: NodePosition;
  // An arm with no nodes of its own renders as a labelled arrow from the
  // fork straight to its exit, with nothing in between -- that is what
  // keeps "the condition was false" selectable instead of invisible.
  isEmptyArm: boolean;
}

const REGION_PAD = 26;
const EMPTY_PANEL_HEIGHT = 46;
const EMPTY_PANEL_TOP_GAP = (ROW_HEIGHT - EMPTY_PANEL_HEIGHT) / 2;
const LABEL_HEIGHT = 16;
const LABEL_CLEARANCE = 10;
const EDGE_PANEL_POSITION = 0.5;
const NESTED_PANEL_GAP = 18;

function overlapsLabel(
  box: { x: number; y: number; width: number; height: number },
  positions: Map<string, NodePosition>,
  labelWidths: Map<string, number>,
): boolean {
  for (const [id, position] of positions) {
    const labelWidth = labelWidths.get(id);
    if (labelWidth === undefined) continue;
    const left = position.x - LABEL_CLEARANCE;
    const right = position.x + labelWidth + LABEL_CLEARANCE;
    const top = position.y - LABEL_HEIGHT / 2 - LABEL_CLEARANCE;
    const bottom = position.y + LABEL_HEIGHT / 2 + LABEL_CLEARANCE;
    if (box.x < right && box.x + box.width > left && box.y < bottom && box.y + box.height > top) {
      return true;
    }
  }
  return false;
}

export function computePanelGeometry(
  panels: BranchPanel[],
  selection: Map<string, string>,
  positions: Map<string, NodePosition>,
  labelWidths: Map<string, number> = new Map(),
  visibleEdges: FlowEdge[] = [],
): PanelGeometry[] {
  const geometries: PanelGeometry[] = [];

  for (const panel of panels) {
    const selectedId = selection.get(panel.id) ?? panel.defaultArmId;
    const arm = panel.arms.find((a) => a.id === selectedId);
    if (!arm) continue;

    const fork = positions.get(panel.branchPointIds[0]);
    const merge = panel.convergesAt ? positions.get(panel.convergesAt) : undefined;

    const shown = arm;
    const memberIds = shown.memberIds;
    const isEmptyArm = memberIds.length === 0;

    // The box is exactly the bounding box of the arm's members -- no
    // clamping to the fork or merge rows.
    //
    // Clamping was wrong and measurably so: it cut 85 member nodes out of
    // their own regions, including the leaf under every `throw new X(...)`
    // and 57 of the root try's 119. Longest-path layering gives a
    // dead-ended throw subtree no reason to sit ABOVE the surviving path's
    // convergence -- they are independent branches of the DAG -- so
    // "everything before the merge row" is not the same set as "everything
    // this arm owns", and the arm is what the panel is about.
    //
    // Convergence-exclusive still holds, and for a better reason than
    // clipping: `convergesAt` belongs to no arm by construction, so it is
    // never a member and never inside the set the box is drawn around.
    const memberBox = computeBBox(memberIds, positions, REGION_PAD);
    const isFallbackBox = memberBox === null;
    const routeTargetIds = panelRouteTargetIds(panel, panels, selection);
    const routeEdge = visibleEdges.find((edge) =>
          routeTargetIds.includes(edge.to) &&
          edge.type === (panel.structure === "DISPATCH" ? "invoke" : "sequence") &&
          (panel.branchPointIds.includes(edge.from) ||
            (edge.returnFrom != null && panel.branchPointIds.includes(edge.returnFrom))),
        );
    const routeFrom = routeEdge ? positions.get(routeEdge.from) : undefined;
    const routeTo = routeEdge ? positions.get(routeEdge.to) : undefined;
    const box = memberBox
      // Nothing to wrap: an empty arm, or one whose nodes are all hidden by
      // an enclosing panel. Put an empty arm directly on its selected route
      // edge. returnFrom-aware matching is important when the branch point
      // invokes a method: the visible edge starts at that callee's return
      // node, not at the original call-site node.
      ?? (routeFrom && routeTo
        ? {
            x: routeFrom.x + (routeTo.x - routeFrom.x) * EDGE_PANEL_POSITION - 130,
            y: routeFrom.y + (routeTo.y - routeFrom.y) * EDGE_PANEL_POSITION
              - EMPTY_PANEL_HEIGHT / 2,
            width: 260,
            height: EMPTY_PANEL_HEIGHT,
          }
        : fork
        ? {
            x: fork.x - REGION_PAD,
            y: fork.y + EMPTY_PANEL_TOP_GAP,
            width: 260,
            height: EMPTY_PANEL_HEIGHT,
          }
        : null);
    if (!box) continue;

    // An empty arm must not obscure the fork's label or any later label.
    // It stays compact and moves down by whole graph rows only when needed.
    if (isFallbackBox && !(routeFrom && routeTo)) {
      let attempts = 0;
      while (overlapsLabel(box, positions, labelWidths) && attempts++ < 100) {
        box.y += ROW_HEIGHT;
      }
    }

    // Node labels are part of the visual node, not decoration outside it.
    // The graph's labels extend to the right of their dots, so widen the
    // panel to contain the longest selected-arm label as well.
    let right = box.x + box.width;
    for (const id of memberIds) {
      const position = positions.get(id);
      if (position) right = Math.max(right, position.x + (labelWidths.get(id) ?? 0) + REGION_PAD);
    }
    box.width = right - box.x;

    // Only content-backed panels need extra headroom above their first node.
    // The compact empty fallback already starts below the fork label.
    if (!isFallbackBox) {
      box.y -= 24;
      box.height += 24;
    }

    geometries.push({
      panel,
      arm: shown,
      x: box.x,
      y: box.y,
      width: box.width,
      height: box.height,
      fork,
      merge,
      isEmptyArm,
    });
  }

  // A node inside nested control structures carries every enclosing arm
  // membership. Use that set containment to make panel containment equally
  // explicit: the parent's border must sit outside the child's with enough
  // air that the two strokes never touch.
  const memberSets = new Map(
    geometries.map((geometry) => [
      geometry.panel.id,
      new Set(geometry.arm?.memberIds ?? []),
    ]),
  );
  const parentOf = new Map<string, PanelGeometry>();
  for (const child of geometries) {
    const childMembers = memberSets.get(child.panel.id)!;
    const candidates = geometries.filter((candidate) => {
      if (candidate.panel.id === child.panel.id) return false;
      const parentMembers = memberSets.get(candidate.panel.id)!;
      const containsMembers = childMembers.size > 0
        && parentMembers.size > childMembers.size
        && [...childMembers].every((id) => parentMembers.has(id));
      // Empty arms have no members to compare, but their fork still lives
      // inside the enclosing arm. This keeps their compact route panel
      // nested instead of letting it sit on the parent's border.
      const containsFork = childMembers.size === 0
        && child.panel.branchPointIds.some((id) => parentMembers.has(id));
      return containsMembers || containsFork;
    });
    const parent = candidates.sort(
      (a, b) => memberSets.get(a.panel.id)!.size - memberSets.get(b.panel.id)!.size,
    )[0];
    if (parent) parentOf.set(child.panel.id, parent);
  }

  // Children first: expanding an intermediate parent before its own parent
  // propagates the complete nested extent outward through every level.
  const byMembershipSize = [...geometries].sort(
    (a, b) => memberSets.get(a.panel.id)!.size - memberSets.get(b.panel.id)!.size,
  );
  for (const child of byMembershipSize) {
    const parent = parentOf.get(child.panel.id);
    if (!parent) continue;
    const left = Math.min(parent.x, child.x - NESTED_PANEL_GAP);
    const top = Math.min(parent.y, child.y - NESTED_PANEL_GAP);
    const right = Math.max(parent.x + parent.width, child.x + child.width + NESTED_PANEL_GAP);
    const bottom = Math.max(parent.y + parent.height, child.y + child.height + NESTED_PANEL_GAP);
    parent.x = left;
    parent.y = top;
    parent.width = right - left;
    parent.height = bottom - top;
  }

  // Outer panels first, nested panels last, so the child stroke remains
  // crisp on top of its containing region.
  return geometries.sort((a, b) => b.width * b.height - a.width * a.height);
}
