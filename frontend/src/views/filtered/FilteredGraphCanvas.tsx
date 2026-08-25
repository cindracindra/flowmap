import { Box, Flex, Text } from "@radix-ui/themes";
import { ChevronDown, ChevronRight } from "lucide-react";
import { useCallback, useMemo, useRef, useState } from "react";

import { opseqLabel } from "../../data/operationLabels";
import { layoutFilteredGraph, visibleNodeLabel, type PhaseGeometry } from "../../lib/filteredGraphLayout";
import type {
  BranchInstanceId,
  VisibleGraphProjection,
  VisibleNode,
} from "../../lib/filteredGraphProjection";
import { shortLabel } from "../../lib/graph";
import { nodeVisualStyle } from "../../lib/nodeStyles";
import { MONO } from "../../lib/ui";
import type { GraphBundle } from "../../types/filteredGraph";
import FilteredGraphSvg from "./FilteredGraphSvg";

function fullLabel(node: VisibleNode): string {
  return node.node.code ?? node.node.calleeFullName ?? node.definitionNodeId;
}

export interface FilteredGraphCanvasProps {
  projection: VisibleGraphProjection;
  selectedNodeId: string | null;
  onSelectNode: (node: VisibleNode) => void;
  onToggleCall: (node: VisibleNode) => void;
  onSelectBranchArm: (branchId: BranchInstanceId, armLabel: string, kind: string) => void;
  bundle: GraphBundle;
  currentMethodEntryId: string | null;
  onSelectMethod: (entryId: string) => void;
  onSelectOperation: (operationId: string) => void;
}

export default function FilteredGraphCanvas({
  projection,
  selectedNodeId,
  onSelectNode,
  onToggleCall,
  onSelectBranchArm,
  bundle,
  currentMethodEntryId,
  onSelectMethod,
  onSelectOperation,
}: FilteredGraphCanvasProps) {
  const [openSummary, setOpenSummary] = useState<"callers" | "operations" | null>(null);
  const [hoveredNode, setHoveredNode] = useState<VisibleNode | null>(null);
  const [graphScrollTop, setGraphScrollTop] = useState(0);
  const graphScrollRef = useRef<HTMLDivElement | null>(null);
  const layout = useMemo(() => layoutFilteredGraph(projection), [projection]);
  const handleHoverNode = useCallback((node: VisibleNode | null) => setHoveredNode(node), []);
  const currentMethod = currentMethodEntryId ? bundle.methodsByEntryId[currentMethodEntryId] : undefined;
  const callerIds = currentMethodEntryId ? bundle.callersByEntryId[currentMethodEntryId] ?? [] : [];
  const operationIds = currentMethodEntryId ? bundle.operationIdsByMethodEntryId[currentMethodEntryId] ?? [] : [];

  return (
    <Flex direction="column" flexGrow="1" style={{ minWidth: 0 }}>
      <Flex align="center" justify="between" px="3" height="38px" flexShrink="0"
        style={{ borderBottom: "1px solid var(--gray-a5)", position: "relative" }}>
        <Text size="1" weight="medium" style={{ fontFamily: MONO }}>
          {currentMethod ? shortLabel(currentMethod.methodFullName) : "Expandable flow"}
        </Text>
        <Flex gap="2">
          <SummaryButton label="operation sequences" count={operationIds.length} open={openSummary === "operations"}
            onClick={() => setOpenSummary((value) => value === "operations" ? null : "operations")} />
          <SummaryButton label="callers" count={callerIds.length} open={openSummary === "callers"}
            onClick={() => setOpenSummary((value) => value === "callers" ? null : "callers")} />
        </Flex>
        {openSummary && (
          <Box style={{ position: "absolute", zIndex: 10, top: 34, right: 10, width: 300,
            maxHeight: 280, overflow: "auto", padding: 6, border: "1px solid var(--gray-a6)",
            borderRadius: 6, background: "var(--color-panel-solid)", boxShadow: "0 8px 24px var(--gray-a5)" }}>
            {(openSummary === "callers" ? callerIds : operationIds).length === 0 ? (
              <Box p="2"><Text size="1" color="gray">No {openSummary}</Text></Box>
            ) : openSummary === "callers" ? callerIds.map((entryId) => {
              const method = bundle.methodsByEntryId[entryId];
              return <SummaryRow key={entryId} label={method ? shortLabel(method.methodFullName) : entryId}
                onClick={() => { onSelectMethod(entryId); setOpenSummary(null); }} />;
            }) : operationIds.map((id) => {
              const operation = bundle.operationsById[id];
              const root = operation && bundle.methodsByEntryId[operation.rootEntryId];
              const label = opseqLabel(id)
                ?? (operation?.label ? shortLabel(operation.label) : root ? shortLabel(root.methodFullName) : id);
              return <SummaryRow key={id} label={label}
                onClick={() => { onSelectOperation(id); setOpenSummary(null); }} />;
            })}
          </Box>
        )}
      </Flex>
      <Flex flexGrow="1" style={{ minHeight: 0, background: "var(--canvas-background)" }}>
        <PhaseLane phases={layout.phases} scrollTop={graphScrollTop} onReveal={(top) => {
          graphScrollRef.current?.scrollTo({ top: Math.max(0, top - 18), behavior: "smooth" });
        }} />
        <Box ref={graphScrollRef} overflow="auto" flexGrow="1" position="relative"
          onScroll={(event) => setGraphScrollTop(event.currentTarget.scrollTop)}>
          <FilteredGraphSvg projection={projection} layout={layout} selectedNodeId={selectedNodeId}
            onSelectNode={onSelectNode} onToggleCall={onToggleCall}
            onSelectBranchArm={onSelectBranchArm} onHoverNode={handleHoverNode} bundle={bundle} />
          {hoveredNode && <NodeTooltip node={hoveredNode} layout={layout} />}
        </Box>
      </Flex>
    </Flex>
  );
}

function PhaseLane({ phases, scrollTop, onReveal }: {
  phases: PhaseGeometry[];
  scrollTop: number;
  onReveal: (top: number) => void;
}) {
  return (
    <Box aria-label="Method-local phase lane" width="156px" flexShrink="0" position="relative"
      style={{ overflow: "hidden", borderRight: "1px solid var(--gray-a5)", background: "var(--color-panel-solid)" }}>
      <Text size="1" weight="bold" color="gray" style={{ position: "absolute", top: 9, left: 12, zIndex: 2 }}>
        EXECUTION PHASES
      </Text>
      {phases.map((phase) => {
        const color = `var(--phase-${phase.colorIndex % 5 + 1})`;
        return (
          <button key={phase.id} title={`Scroll to ${phase.label}`} onClick={() => onReveal(phase.y)}
            style={{ all: "unset", boxSizing: "border-box", position: "absolute",
              top: phase.y - scrollTop, left: 8 + phase.depth * 8,
              width: Math.max(72, 140 - phase.depth * 8), height: Math.max(34, phase.height),
              padding: "7px 8px 6px 11px", borderLeft: `4px solid ${color}`,
              borderRadius: "0 5px 5px 0", background: `color-mix(in srgb, ${color} 8%, transparent)`,
              color: "var(--canvas-foreground)", fontFamily: MONO, fontSize: 10,
              cursor: "pointer", overflow: "hidden" }}>
            <span style={{ display: "block", fontWeight: 600, color }}>{phase.label}</span>
          </button>
        );
      })}
    </Box>
  );
}

function NodeTooltip({ node, layout }: { node: VisibleNode; layout: ReturnType<typeof layoutFilteredGraph> }) {
  const point = layout.positions.get(node.id);
  if (!point) return null;
  const style = nodeVisualStyle(node.node);
  return (
    <Box role="tooltip" style={{ position: "absolute", left: point.x + style.radius + 14,
      top: point.y + 14, zIndex: 8, maxWidth: 360, padding: "7px 9px", pointerEvents: "none",
      border: "1px solid var(--gray-a6)", borderRadius: 5, background: "var(--color-panel-solid)",
      boxShadow: "0 5px 18px var(--gray-a5)" }}>
      <Text size="1" weight="bold" as="div" style={{ fontFamily: MONO, overflowWrap: "anywhere" }}>
        {node.node.type === "entry" || node.node.exitKind === "fallthrough" ? visibleNodeLabel(node) : fullLabel(node)}
      </Text>
      <Text size="1" color="gray" as="div" mt="1" style={{ fontFamily: MONO }}>
        {style.label}{node.node.sourceFile ? ` · ${node.node.sourceFile}${node.node.line ? `:${node.node.line}` : ""}` : ""}
      </Text>
    </Box>
  );
}

function SummaryButton({ label, count, open, onClick }: { label: string; count: number; open: boolean; onClick: () => void }) {
  return <button aria-expanded={open} onClick={onClick} style={{ all: "unset", display: "flex",
    alignItems: "center", gap: 4, cursor: "pointer", padding: "3px 7px", borderRadius: 4,
    color: "var(--gray-11)", background: open ? "var(--gray-a4)" : "var(--gray-a3)",
    fontFamily: MONO, fontSize: 11 }}>
    {open ? <ChevronDown size={11} /> : <ChevronRight size={11} />}{count} {label}
  </button>;
}

function SummaryRow({ label, onClick }: { label: string; onClick: () => void }) {
  return <button onClick={onClick} style={{ all: "unset", boxSizing: "border-box", display: "block",
    width: "100%", padding: "6px 8px", cursor: "pointer", borderRadius: 4,
    color: "var(--gray-11)", fontFamily: MONO, fontSize: 11, overflow: "hidden",
    textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={label}>{label}</button>;
}
