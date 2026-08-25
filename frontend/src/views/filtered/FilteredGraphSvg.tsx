import { memo, useId } from "react";
import { Repeat2 } from "lucide-react";

import type { FilteredGraphLayout } from "../../lib/filteredGraphLayout";
import {
  branchArmText,
  dispatchArmLabel,
  dispatchArmWidth,
  truncateBranchText,
  visibleNodeLabel,
} from "../../lib/filteredGraphLayout";
import type {
  BranchInstanceId,
  VisibleGraphProjection,
  VisibleNode,
} from "../../lib/filteredGraphProjection";
import { visibleNodeLoops } from "../../lib/filteredGraphLoops";
import { EDGE_ARROW_SIZE, EDGE_STYLES, nodeVisualStyle } from "../../lib/nodeStyles";
import { MONO } from "../../lib/ui";
import type { GraphBundle } from "../../types/filteredGraph";

interface FilteredGraphSvgProps {
  projection: VisibleGraphProjection;
  layout: FilteredGraphLayout;
  selectedNodeId: string | null;
  onSelectNode: (node: VisibleNode) => void;
  onToggleCall: (node: VisibleNode) => void;
  onSelectBranchArm: (branchId: BranchInstanceId, armLabel: string, kind: string) => void;
  onHoverNode: (node: VisibleNode | null) => void;
  bundle: GraphBundle;
}

/** Static graph layer: tooltip state changes must not reconcile this SVG. */
function FilteredGraphSvgComponent({
  projection,
  layout: { positions, branches, width, height },
  selectedNodeId,
  onSelectNode,
  onToggleCall,
  onSelectBranchArm,
  onHoverNode,
  bundle,
}: FilteredGraphSvgProps) {
  const markerScope = useId().replaceAll(":", "");
  const sequenceMarkerId = `filtered-sequence-arrow-${markerScope}`;
  const invokeMarkerId = `filtered-invoke-arrow-${markerScope}`;
  return (
    <svg width={width} height={height} role="img" aria-label="Expandable filtered control flow">
      <defs>
        <marker id={sequenceMarkerId} markerWidth={EDGE_ARROW_SIZE} markerHeight={EDGE_ARROW_SIZE} refX={EDGE_ARROW_SIZE - 1} refY={EDGE_ARROW_SIZE / 2} orient="auto">
          <path d={`M 0 0 L ${EDGE_ARROW_SIZE} ${EDGE_ARROW_SIZE / 2} L 0 ${EDGE_ARROW_SIZE} z`} fill={EDGE_STYLES.sequence.color} />
        </marker>
        <marker id={invokeMarkerId} markerWidth={EDGE_ARROW_SIZE} markerHeight={EDGE_ARROW_SIZE} refX={EDGE_ARROW_SIZE - 1} refY={EDGE_ARROW_SIZE / 2} orient="auto">
          <path d={`M 0 0 L ${EDGE_ARROW_SIZE} ${EDGE_ARROW_SIZE / 2} L 0 ${EDGE_ARROW_SIZE} z`} fill={EDGE_STYLES.invoke.color} />
        </marker>
      </defs>
      {branches.map(({ group, x, y, width: branchWidth, height: branchHeight }) => {
        const color = group.kind === "DISPATCH" ? "var(--panel-polymorphic)" : "var(--panel-conditional)";
        return <rect key={`branch-region:${group.id}`} x={x} y={y} width={branchWidth} height={branchHeight} rx="12"
          fill={color} fillOpacity="0.045" stroke={color}
          strokeOpacity="0.58" strokeWidth="1.2" strokeDasharray={group.kind === "DISPATCH" ? "5 4" : undefined} />;
      })}
      {projection.edges.map((edge, index) => {
        const from = positions.get(edge.from);
        const to = positions.get(edge.to);
        if (!from || !to) return null;
        const invoke = edge.kind === "invoke";
        const style = EDGE_STYLES[edge.kind];
        const path = invoke
          ? `M ${from.x + 12} ${from.y} C ${from.x + 70} ${from.y}, ${to.x - 70} ${to.y}, ${to.x - 12} ${to.y}`
          : `M ${from.x} ${from.y + 12} C ${from.x} ${from.y + 36}, ${to.x} ${to.y - 36}, ${to.x} ${to.y - 12}`;
        return <path key={`${edge.kind}:${edge.from}:${edge.to}:${index}`} d={path} fill="none"
          stroke={style.color} strokeWidth="1.2" strokeDasharray={style.dash} opacity="0.8"
          markerEnd={`url(#${invoke ? invokeMarkerId : sequenceMarkerId})`}><title>{style.label}</title></path>;
      })}
      {projection.branchGroups.filter((group) => group.kind === "DISPATCH").map((group) => {
        const selectedArm = group.arms.find((arm) => arm.label === group.selectedArmLabel);
        const entry = selectedArm?.firstCallId ? positions.get(selectedArm.firstCallId) : undefined;
        const fallback = (group.branchPointIds ?? []).map((id) => positions.get(id)).find(Boolean);
        const anchor = entry ?? fallback;
        if (!anchor) return null;
        const labels = group.arms.map(dispatchArmLabel);
        const widths = group.arms.map(dispatchArmWidth);
        const selectorX = anchor.x;
        let cursorX = selectorX + 58;
        const y = entry ? entry.y - 38 : anchor.y + 28;
        const color = "var(--panel-polymorphic)";
        return (
          <g key={`dispatch-controls:${group.id}`}>
            <text x={selectorX} y={y + 14} fontSize="10" fontWeight="600"
              fontFamily={MONO} fill={color}>dispatch</text>
            {group.arms.map((arm, index) => {
              const label = labels[index];
              const buttonWidth = widths[index];
              const buttonX = cursorX;
              cursorX += buttonWidth + 6;
              const selected = arm.label === group.selectedArmLabel;
              return (
                <g key={arm.label} role="button" tabIndex={0}
                  aria-label={`Dispatch to ${label}`} aria-pressed={selected}
                  onClick={(event) => {
                    event.stopPropagation();
                    onSelectBranchArm(group.id, arm.label, group.kind);
                  }}
                  onKeyDown={(event) => {
                    if (event.key !== "Enter" && event.key !== " ") return;
                    event.preventDefault();
                    onSelectBranchArm(group.id, arm.label, group.kind);
                  }} style={{ cursor: "pointer" }}>
                  <title>{arm.conditionCode ?? arm.label}</title>
                  <rect x={buttonX} y={y} width={buttonWidth} height="22" rx="11"
                    fill={color} fillOpacity={selected ? 0.3 : 0.08}
                    stroke={color} strokeOpacity={selected ? 1 : 0.55}
                    strokeWidth={selected ? 1.8 : 1} />
                  <text x={buttonX + buttonWidth / 2} y={y + 14.5} textAnchor="middle"
                    fontSize="10" fontWeight={selected ? "600" : "400"} fontFamily={MONO}
                    fill="var(--canvas-foreground)" pointerEvents="none">{label}</text>
                </g>
              );
            })}
          </g>
        );
      })}
      {projection.nodes.map((node) => {
        const point = positions.get(node.id)!;
        const style = nodeVisualStyle(node.node);
        const selected = selectedNodeId === node.id;
        const loops = visibleNodeLoops(node, bundle);
        return (
          <g key={node.id} className="filtered-graph-node" transform={`translate(${point.x} ${point.y})`}
            onClick={() => {
              onSelectNode(node);
              if (node.expandable && !node.recursiveCutoff) onToggleCall(node);
            }}
            onMouseEnter={() => onHoverNode(node)} onMouseLeave={() => onHoverNode(null)}
            style={{ cursor: node.expandable && !node.recursiveCutoff ? "pointer" : "default" }}>
            <circle className="filtered-graph-node-hover" r={style.radius + 6} fill={style.stroke + "28"}
              opacity={selected ? 1 : 0} style={{ transition: "opacity 80ms" }} />
            {loops.length > 0 && (
              <g pointerEvents="none">
                <title>{`Inside ${loops.map((loop) => loop.label).join("; ")}`}</title>
                <Repeat2 x={-style.radius - 18} y={-6} width={12} height={12}
                  color="var(--orange-9)" strokeWidth={1.8} />
              </g>
            )}
            {style.shape === "diamond"
              ? <rect x={-style.radius * 0.75} y={-style.radius * 0.75} width={style.radius * 1.5}
                  height={style.radius * 1.5} transform="rotate(45)" fill={style.fill} stroke={style.stroke}
                  strokeWidth={selected ? 2 : 1} />
              : <circle r={style.radius} fill={style.fill} stroke={style.stroke} strokeWidth={selected ? 2 : 1}
                  strokeDasharray={style.strokeDasharray} />}
            {selected && <circle r={style.radius * 0.35} fill={style.stroke} opacity={0.7} />}
            {node.expandable && <text x="0" y="4" textAnchor="middle" fontSize="12" fill={style.stroke}>
              {node.recursiveCutoff ? "↻" : node.expanded ? "−" : "+"}
            </text>}
            <text x={style.radius + 9} y="4" fontSize="11" fontFamily={MONO} fill="var(--canvas-foreground)">{visibleNodeLabel(node)}</text>
            {node.node.type === "exit" && <text x="18" y="19" fontSize="9" fontFamily={MONO} fill={style.stroke}>
              {node.node.exitKind === "fallthrough" ? "implicit method end" : node.node.exitKind === "throw" ? "dead end" : "explicit return"}
            </text>}
            {node.retainedCall && <text x="18" y="19" fontSize="9" fontFamily={MONO} fill="var(--accent-11)">
              {`${node.retainedCalleePhaseCount ?? 0} ${node.retainedCalleePhaseCount === 1 ? "phase" : "phases"} inside`}
            </text>}
          </g>
        );
      })}
      {branches.map(({ group, x, y }) => {
        let cursorX = x + 70;
        const color = group.kind === "DISPATCH" ? "var(--panel-polymorphic)" : "var(--panel-conditional)";
        return (
          <g key={`branch-controls:${group.id}`}>
            <text x={x + 9} y={y + 18} fontSize="10" fontWeight="600" fontFamily={MONO} fill={color}>{group.kind === "DISPATCH" ? "dispatch" : group.kind}</text>
            {group.arms.map((arm) => {
              const fullText = branchArmText(arm);
              const label = truncateBranchText(fullText);
              const buttonWidth = Math.max(58, label.length * 6.2 + 18);
              const selected = arm.label === group.selectedArmLabel;
              const buttonX = cursorX;
              cursorX += buttonWidth + 6;
              return (
                <g key={arm.label} role="button" tabIndex={0} aria-label={`Select ${group.kind} arm ${fullText}`}
                  aria-pressed={selected} onClick={(event) => { event.stopPropagation(); onSelectBranchArm(group.id, arm.label, group.kind); }}
                  onKeyDown={(event) => {
                    if (event.key !== "Enter" && event.key !== " ") return;
                    event.preventDefault();
                    onSelectBranchArm(group.id, arm.label, group.kind);
                  }} style={{ cursor: "pointer" }}>
                  <title>{fullText}</title>
                  <rect x={buttonX} y={y + 5} width={buttonWidth} height="20" rx="10"
                    fill={color} fillOpacity={selected ? 0.3 : 0.08}
                    stroke={color} strokeOpacity={selected ? 1 : 0.45}
                    strokeWidth={selected ? 1.8 : 1} />
                  <text x={buttonX + buttonWidth / 2} y={y + 18.5} textAnchor="middle" fontSize="9"
                    fontWeight={selected ? "600" : "400"} fontFamily={MONO}
                    fill="var(--canvas-foreground)" pointerEvents="none">{label}</text>
                </g>
              );
            })}
          </g>
        );
      })}
      <style>{`.filtered-graph-node:hover > .filtered-graph-node-hover { opacity: 1; }`}</style>
    </svg>
  );
}

export default memo(FilteredGraphSvgComponent);
