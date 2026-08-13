// Where a branch panel is drawn on the canvas.
//
// The region covers the branch's TERRITORY: everything between the fork and
// the convergence point, convergence EXCLUSIVE. The merge node is where the
// branch has stopped mattering -- both alternatives reach it -- so it sits
// outside the box, and the box's bottom edge is the visual statement of
// "past here, the choice no longer applies".

import type { BranchPanel, PanelArm } from "./branches";
import { computeBBox } from "./graph";
import type { NodePosition } from "./layout";

export interface PanelGeometry {
  panel: BranchPanel;
  // The arm currently on screen. null for a TRY with no exception selected,
  // which is a real state and draws the spine with no alternative region.
  arm: PanelArm | null;
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
// Clear of the fork/merge node glyphs so the boundary reads as excluding
// them rather than clipping them.
const BOUNDARY_GAP = 30;

export function computePanelGeometry(
  panels: BranchPanel[],
  selection: Map<string, string | null>,
  positions: Map<string, NodePosition>,
): PanelGeometry[] {
  const geometries: PanelGeometry[] = [];

  for (const panel of panels) {
    const selectedId = selection.get(panel.id) ?? null;
    const arm = panel.arms.find((a) => a.id === selectedId) ?? null;

    const fork = positions.get(panel.branchPointIds[0]);
    const merge = panel.convergesAt ? positions.get(panel.convergesAt) : undefined;

    // A TRY always shows its spine, so its region is drawn even with no
    // catch selected -- the box is the try body's own extent.
    const spine = panel.arms.find((a) => a.role === "spine");
    const shown = arm ?? spine ?? null;
    const memberIds = shown ? shown.memberIds : [];
    const isEmptyArm = shown !== null && memberIds.length === 0;

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
    const box = computeBBox(memberIds, positions, REGION_PAD)
      // Nothing to wrap: an empty arm, or one whose nodes are all hidden by
      // an enclosing panel. A slim band off the fork gives the switcher
      // somewhere to live and the empty arm somewhere to draw its arrow.
      ?? (fork
        ? { x: fork.x - REGION_PAD, y: fork.y + BOUNDARY_GAP, width: 260, height: 46 }
        : null);
    if (!box) continue;

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

  // Widest first, so a nested panel paints on top of the one containing it
  // instead of disappearing underneath it.
  return geometries.sort((a, b) => b.width * b.height - a.width * a.height);
}
