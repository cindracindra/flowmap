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
const NESTED_PANEL_GAP = 18;

interface EmptyRoutePlacement {
  from: NodePosition;
  to: NodePosition;
  position: number;
}

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

function invokedSubtreeBottom(
  panel: BranchPanel,
  positions: Map<string, NodePosition>,
  visibleEdges: FlowEdge[],
): number | undefined {
  const points = new Set(panel.branchPointIds);
  const outgoing = new Map<string, FlowEdge[]>();
  for (const edge of visibleEdges) {
    const edges = outgoing.get(edge.from);
    if (edges) edges.push(edge);
    else outgoing.set(edge.from, [edge]);
  }
  const stack = visibleEdges
    .filter((edge) => edge.type === "invoke" && points.has(edge.from))
    .map((edge) => edge.to);
  const visited = new Set<string>();
  let bottom: number | undefined;
  while (stack.length > 0) {
    const id = stack.pop()!;
    if (visited.has(id)) continue;
    visited.add(id);
    const position = positions.get(id);
    if (position) bottom = Math.max(bottom ?? position.y, position.y);
    for (const edge of outgoing.get(id) ?? []) {
      // A return/fallback attributed to the TRY tail has left the protected
      // call. Nested returns remain part of that call's visible subtree.
      if (edge.returnFrom != null && points.has(edge.returnFrom)) continue;
      stack.push(edge.to);
    }
  }
  return bottom;
}

export function computePanelGeometry(
  panels: BranchPanel[],
  selection: Map<string, string>,
  positions: Map<string, NodePosition>,
  labelWidths: Map<string, number> = new Map(),
  visibleEdges: FlowEdge[] = [],
): PanelGeometry[] {
  const geometries: PanelGeometry[] = [];

  // Several sequential conditions can share one visible edge when their
  // condition nodes were stripped from the graph. Their empty arms are still
  // separate, selectable choices, so give each panel its own slot along that
  // edge instead of putting every one at the midpoint.
  const emptyRoutes = new Map<
    string,
    { panel: BranchPanel; from: NodePosition; to: NodePosition }[]
  >();
  for (const panel of panels) {
    const selectedId = selection.get(panel.id) ?? panel.defaultArmId;
    const arm = panel.arms.find((candidate) => candidate.id === selectedId);
    if (!arm?.empty) continue;
    const routeTargetIds = panelRouteTargetIds(panel, panels, selection);
    const routeEdge = visibleEdges.find((edge) =>
      routeTargetIds.includes(edge.to)
      && edge.type === (panel.structure === "DISPATCH" ? "invoke" : "sequence")
      && (panel.branchPointIds.includes(edge.from)
        || (edge.returnFrom != null && panel.branchPointIds.includes(edge.returnFrom))),
    );
    if (!routeEdge) continue;
    const from = positions.get(routeEdge.from);
    const to = positions.get(routeEdge.to);
    if (!from || !to) continue;
    const key = `${routeEdge.from}\u0000${routeEdge.to}\u0000${routeEdge.type}`;
    const group = emptyRoutes.get(key);
    const route = { panel, from, to };
    if (group) group.push(route);
    else emptyRoutes.set(key, [route]);
  }
  const emptyRoutePlacements = new Map<string, EmptyRoutePlacement>();
  for (const routes of emptyRoutes.values()) {
    routes.forEach((route, index) => {
      let position = (index + 1) / (routes.length + 1);
      // A TRY selector describes what happened after its protected body.
      // With a single empty noCatch route, keep it next to the continuation
      // rather than centring it over the callee work that preceded it.
      if (routes.length === 1 && route.panel.switcherPosition === "after") {
        const desiredCenter = route.to.y - EMPTY_PANEL_HEIGHT / 2 - 8;
        const deltaY = route.to.y - route.from.y;
        position = deltaY > 0
          ? Math.max(0, Math.min(1, (desiredCenter - route.from.y) / deltaY))
          : 0.75;
      }
      emptyRoutePlacements.set(route.panel.id, {
        from: route.from,
        to: route.to,
        position,
      });
    });
  }

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
    const emptyPlacement = emptyRoutePlacements.get(panel.id);
    const routeFrom = emptyPlacement?.from ?? (routeEdge ? positions.get(routeEdge.from) : undefined);
    const routeTo = emptyPlacement?.to ?? (routeEdge ? positions.get(routeEdge.to) : undefined);
    const routePosition = emptyPlacement?.position ?? 0.5;
    const subtreeBottom = panel.switcherPosition === "after"
      ? invokedSubtreeBottom(panel, positions, visibleEdges)
      : undefined;
    const box = memberBox
      // Nothing to wrap: an empty arm, or one whose nodes are all hidden by
      // an enclosing panel. Put an empty arm directly on its selected route
      // edge. returnFrom-aware matching is important when the branch point
      // invokes a method: the visible edge starts at that callee's return
      // node, not at the original call-site node.
      ?? (routeFrom && routeTo
        ? {
            x: routeFrom.x + (routeTo.x - routeFrom.x) * routePosition - 130,
            y: routeFrom.y + (routeTo.y - routeFrom.y) * routePosition
              - EMPTY_PANEL_HEIGHT / 2,
            width: 260,
            height: EMPTY_PANEL_HEIGHT,
          }
        : fork
        ? {
            x: fork.x - REGION_PAD,
            y: subtreeBottom === undefined
              ? fork.y + EMPTY_PANEL_TOP_GAP
              : Math.max(
                  fork.y + EMPTY_PANEL_TOP_GAP,
                  subtreeBottom + REGION_PAD + 8,
                ),
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
