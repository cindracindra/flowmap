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
// estimate reliable enough for compact selection badges.
const CHAR_W = 6.1;
const pillWidth = (label: string) => Math.max(46, label.length * CHAR_W + 18);

function armSwitcherLabel(arm: PanelArm): string {
  return arm.label.length > 26 ? `${arm.label.slice(0, 25)}…` : arm.label;
}

/** The region containers. Drawn behind edges and nodes. */
export function BranchRegions({
  geometries,
  activeId,
  onSelect,
  onHover,
}: {
  geometries: PanelGeometry[];
  activeId: string | null;
  onSelect: (panelId: string) => void;
  onHover: (panelId: string | null) => void;
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
              pointerEvents="stroke"
              role="button"
              aria-label={`Show ${geometry.panel.title} branch details`}
              tabIndex={0}
              onClick={(event) => {
                event.stopPropagation();
                onSelect(geometry.panel.id);
              }}
              onKeyDown={(event) => {
                if (event.key !== "Enter" && event.key !== " ") return;
                event.preventDefault();
                onSelect(geometry.panel.id);
              }}
              onMouseEnter={() => onHover(geometry.panel.id)}
              onMouseLeave={() => onHover(null)}
              style={{
                cursor: "pointer",
                transition: "fill-opacity 0.15s, stroke-opacity 0.15s",
              }}
            />
          </g>
        );
      })}
    </g>
  );
}

/** Header and chosen-arm badge. Drawn on top of nodes. */
export function BranchSwitchers({
  geometries,
  selection,
  activeId,
  onSelect,
  onHover,
}: {
  geometries: PanelGeometry[];
  selection: Map<string, string>;
  activeId: string | null;
  onSelect: (panelId: string, armId: string) => void;
  onHover: (panelId: string | null) => void;
}) {
  return (
    <g>
      {/* A compact header strip inside each panel. */}
      {geometries.map((geometry) => (
        <rect
          key={`hit-${geometry.panel.id}`}
          x={geometry.x}
          y={geometry.y}
          width={geometry.width}
          height={28}
          fill="transparent"
          style={{ cursor: "pointer" }}
          onMouseEnter={() => onHover(geometry.panel.id)}
        />
      ))}

      {geometries.map((geometry) => {
        const { panel } = geometry;
        const style = PANEL_KIND[panel.kind];
        const isActive = panel.id === activeId;
        const selected = selection.get(panel.id) ?? panel.defaultArmId;
        const alternatives = panel.arms.filter((a) => a.role === "alternative");

        const options = alternatives.map((a) => ({ id: a.id, label: armSwitcherLabel(a) }));
        if (options.length === 0) return null;

        const selectedOption = options.find((option) => option.id === selected) ?? options[0];
        const selectedArm = alternatives.find((arm) => arm.id === selected)
          ?? alternatives[0];
        const nextOption = options[(options.findIndex((option) => option.id === selected) + 1) % options.length];
        const badgeWidth = pillWidth(selectedOption.label);

        // Every panel keeps the selected-arm badge; only the hovered one
        // expands to expose the clickable condition/dispatch label.
        if (!isActive) {
          return (
            <g key={panel.id} pointerEvents="none">
              <rect
                x={geometry.x + geometry.width - badgeWidth - 8}
                y={geometry.y + 4}
                width={badgeWidth}
                height={17}
                rx={8.5}
                fill={style.fill}
                fillOpacity={0.2}
                stroke={style.stroke}
                strokeOpacity={0.6}
                strokeDasharray={style.dash}
              />
              <text
                x={geometry.x + geometry.width - badgeWidth / 2 - 8}
                y={geometry.y + 16}
                fontSize="9"
                fontFamily={MONO}
                textAnchor="middle"
                fill="var(--canvas-foreground)"
              >
                {selectedOption.label}
              </text>
            </g>
          );
        }
        return (
          <g
            key={panel.id}
            onMouseEnter={() => onHover(panel.id)}
            onMouseLeave={() => onHover(null)}
          >
            <text
              x={geometry.x + 10}
              y={geometry.y + 17}
              fontSize="10"
              fontFamily={MONO}
              fill="var(--canvas-foreground)"
            >
              {style.glyph} {style.badge}
              {panel.title !== style.badge ? ` · ${panel.title}` : ""}
            </text>
            <g
              className="graph-node"
              style={{ cursor: "pointer" }}
              onClick={(event) => {
                event.stopPropagation();
                onSelect(panel.id, nextOption.id);
              }}
            >
              <title>{selectedArm.label}</title>
              <rect
                x={geometry.x + geometry.width - badgeWidth - 8}
                y={geometry.y + 5}
                width={badgeWidth}
                height={17}
                rx={8.5}
                fill={style.fill}
                fillOpacity={0.3}
                stroke={style.stroke}
                strokeDasharray={style.dash}
              />
              <text
                x={geometry.x + geometry.width - badgeWidth / 2 - 8}
                y={geometry.y + 17}
                fontSize="9"
                fontFamily={MONO}
                textAnchor="middle"
                fill="var(--canvas-foreground)"
              >
                {selectedOption.label}
              </text>
            </g>

          </g>
        );
      })}
    </g>
  );
}
