// The branch panel drawn ON the graph: a container around everything one
// alternative causes to happen, bounded by the fork above and the
// convergence below, with the switcher sitting on the boundary.
//
// The two kinds are distinguished three ways, never by colour alone, because
// telling them apart is the point:
//
//   conditional -- solid border, fork glyph, arm labels are the source
//                  conditions ("amount > 1000"), badge reads "condition".
//   polymorphic -- dashed border, diamond glyph, arm labels are implementing
//                  class names, badge reads "dispatch".

import type { BranchPanel, PanelArm } from "../lib/branches";
import type { PanelGeometry } from "../lib/panelGeometry";

const MONO = "'Geist Mono', ui-monospace, SFMono-Regular, monospace";

interface KindStyle {
  stroke: string;
  fill: string;
  dash?: string;
  glyph: string;
  badge: string;
}

export const PANEL_KIND: Record<BranchPanel["kind"], KindStyle> = {
  conditional: {
    stroke: "var(--panel-conditional)",
    fill: "var(--panel-conditional)",
    glyph: "⑂",
    badge: "condition",
  },
  polymorphic: {
    stroke: "var(--panel-polymorphic)",
    fill: "var(--panel-polymorphic)",
    dash: "7 4",
    glyph: "◈",
    badge: "dispatch",
  },
};

// SVG has no text metrics without measuring, and a monospace face makes the
// estimate reliable enough for pill widths.
const CHAR_W = 6.1;
const pillWidth = (label: string) => Math.max(46, label.length * CHAR_W + 18);

function armSwitcherLabel(arm: PanelArm): string {
  return arm.label.length > 26 ? `${arm.label.slice(0, 25)}…` : arm.label;
}

/** The region containers. Drawn behind edges and nodes. */
export function BranchRegions({
  geometries,
  activeId,
}: {
  geometries: PanelGeometry[];
  activeId: string | null;
}) {
  return (
    <g>
      {geometries.map((geometry) => {
        const style = PANEL_KIND[geometry.panel.kind];
        const isActive = geometry.panel.id === activeId;
        return (
          <g key={geometry.panel.id}>
            <rect
              x={geometry.x}
              y={geometry.y}
              width={geometry.width}
              height={geometry.height}
              rx={12}
              fill={style.fill}
              fillOpacity={isActive ? 0.1 : 0.04}
              stroke={style.stroke}
              strokeOpacity={isActive ? 0.95 : 0.5}
              strokeWidth={isActive ? 1.8 : 1.1}
              strokeDasharray={style.dash}
              style={{ transition: "fill-opacity 0.15s, stroke-opacity 0.15s" }}
            />
            {/* The convergence marker sits OUTSIDE the region, on the merge
                node itself -- the visual statement of "the branch ends
                here, and this node belongs to neither alternative". */}
            {geometry.merge && (
              <g opacity={isActive ? 0.9 : 0.35}>
                <line
                  x1={geometry.merge.x - 20}
                  y1={geometry.merge.y - 18}
                  x2={geometry.merge.x + 20}
                  y2={geometry.merge.y - 18}
                  stroke={style.stroke}
                  strokeWidth={1}
                  strokeDasharray="3 2"
                />
                <text
                  x={geometry.merge.x + 24}
                  y={geometry.merge.y - 15}
                  fontSize="8"
                  fontFamily={MONO}
                  fill={style.stroke}
                >
                  converges
                </text>
              </g>
            )}
          </g>
        );
      })}
    </g>
  );
}

/** Header, switcher pills and empty-arm arrow. Drawn on top of nodes. */
export function BranchSwitchers({
  geometries,
  selection,
  activeId,
  onSelect,
  onHover,
}: {
  geometries: PanelGeometry[];
  selection: Map<string, string | null>;
  activeId: string | null;
  onSelect: (panelId: string, armId: string | null) => void;
  onHover: (panelId: string | null) => void;
}) {
  return (
    <g>
      {/* Hover targets for EVERY panel, in the top layer.
          They cannot live with the regions: those are painted behind the
          edges and nodes, so a strip down there only receives a pointer
          where nothing happens to cover it -- which is most of the time
          nowhere. A thin strip on the region's top edge (never its fill,
          which would swallow every click on the nodes inside) is enough of
          a target, and smallest-last means a nested panel wins the hover
          over the one containing it. */}
      {geometries.map((geometry) => (
        <rect
          key={`hit-${geometry.panel.id}`}
          x={geometry.x}
          y={geometry.y - 8}
          width={geometry.width}
          height={20}
          fill="transparent"
          style={{ cursor: "pointer" }}
          onMouseEnter={() => onHover(geometry.panel.id)}
        />
      ))}

      {geometries.map((geometry) => {
        const { panel } = geometry;
        // Only the panel under the cursor gets a header and switcher.
        // Drawing all 15 at once is the overcrowding the panel exists to
        // prevent -- the regions stay visible so the structure reads at a
        // glance, but the controls appear where you are looking.
        if (panel.id !== activeId) return null;
        const style = PANEL_KIND[panel.kind];
        const isActive = true;
        const selected = selection.get(panel.id) ?? null;
        const alternatives = panel.arms.filter((a) => a.role === "alternative");

        // A TRY's switcher belongs AFTER the try body -- by the time it can
        // throw, the body has already run -- so it sits on the bottom edge.
        const rowY =
          panel.switcherPosition === "after"
            ? geometry.y + geometry.height + 4
            : geometry.y - 26;

        // "no exception" is a real option, not the absence of one.
        const options: { id: string | null; label: string }[] = [
          ...(panel.structure === "TRY" ? [{ id: null, label: "no exception" }] : []),
          ...alternatives.map((a) => ({ id: a.id as string | null, label: armSwitcherLabel(a) })),
        ];

        let cursor = geometry.x + 8;
        return (
          <g
            key={panel.id}
            onMouseEnter={() => onHover(panel.id)}
            onMouseLeave={() => onHover(null)}
          >
            {/* Header: glyph + kind badge + what the branch is */}
            <text
              x={geometry.x + 10}
              y={geometry.y - 32}
              fontSize="11"
              fontFamily={MONO}
              fill={style.stroke}
              opacity={isActive ? 1 : 0.75}
            >
              {style.glyph} {style.badge} · {panel.title}
            </text>

            {options.map((option) => {
              const width = pillWidth(option.label);
              const x = cursor;
              cursor += width + 5;
              const on = option.id === selected;
              return (
                <g
                  key={option.id ?? "__none__"}
                  className="graph-node"
                  style={{ cursor: "pointer" }}
                  onClick={(event) => {
                    event.stopPropagation();
                    onSelect(panel.id, option.id);
                  }}
                >
                  <rect
                    x={x}
                    y={rowY}
                    width={width}
                    height={19}
                    rx={9.5}
                    fill={on ? style.fill : "var(--canvas-card)"}
                    fillOpacity={on ? 0.3 : 0.9}
                    stroke={style.stroke}
                    strokeOpacity={on ? 1 : 0.4}
                    strokeWidth={on ? 1.4 : 0.9}
                    strokeDasharray={style.dash}
                  />
                  <text
                    x={x + width / 2}
                    y={rowY + 13}
                    fontSize="9"
                    fontFamily={MONO}
                    textAnchor="middle"
                    fill={on ? style.stroke : "var(--canvas-muted)"}
                    style={{ userSelect: "none" }}
                  >
                    {option.label}
                  </text>
                </g>
              );
            })}

            {/* An arm with no calls: no node to draw, so the arrow and its
                label carry the whole meaning ("else -> skips to X"). */}
            {geometry.isEmptyArm && geometry.fork && (
              <g opacity={isActive ? 0.95 : 0.55}>
                <path
                  d={`M ${geometry.fork.x} ${geometry.fork.y + 16}
                      L ${geometry.fork.x} ${geometry.y + geometry.height / 2}`}
                  stroke={style.stroke}
                  strokeWidth={1.2}
                  strokeDasharray="4 3"
                  fill="none"
                />
                <text
                  x={geometry.x + 14}
                  y={geometry.y + geometry.height / 2 + 4}
                  fontSize="9"
                  fontFamily={MONO}
                  fill={style.stroke}
                >
                  {geometry.arm?.exitKind === "throws"
                    ? "throws — never rejoins"
                    : "no calls — skips ahead"}
                </text>
              </g>
            )}
          </g>
        );
      })}
    </g>
  );
}
