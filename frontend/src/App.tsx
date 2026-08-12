import { useState, useRef, useCallback, useEffect, useMemo } from "react";
import {
  Box,
  Flex,
  Text,
  Heading,
  Badge,
  Button,
  IconButton,
  TextField,
  ScrollArea,
  Separator,
  Tabs,
  Tooltip,
  Card,
} from "@radix-ui/themes";
import {
  ChevronRight,
  ChevronDown,
  ZoomIn,
  ZoomOut,
  Maximize2,
  X,
  GitBranch,
  Upload,
  Settings,
  Minimize2,
  Search,
  FolderOpen,
  Package as PackageIcon,
  Hash,
  ArrowRight,
  AlertTriangle,
} from "lucide-react";

import flattenedRaw from "./data/flattened_cfg.json";
import phaseTreeRaw from "./data/phase_tree.json";
import { CLASS_FILES } from "./data/classFiles";
import type { FlowGraph, FlowNode, FlowEdge, NodeType, PhaseTree, Transition } from "./types/flowmap";
import { computeLayout, type NodePosition } from "./lib/layout";
import {
  shortLabel,
  ownerClassOf,
  indexNodesById,
  buildExplorerTree,
  phaseIndexForNode,
  incomingEdges,
  outgoingEdges,
  computePhaseBBox,
  TRANSITION_REASON_LABELS,
  type ExplorerItem,
} from "./lib/graph";

const MONO = "'Geist Mono', ui-monospace, SFMono-Regular, monospace";

// ── Static sample data ──────────────────────────────────────────────────
// Real Joern-extracted output for one entry point (Main.runTransfer), taken
// from test_code/java_project/output/{flattened_cfg,phase_tree}.json — see
// DESIGN.md §8.6 for the pipeline stage this came from. No backend API
// exists yet, so this is loaded statically rather than fetched.

const GRAPH = flattenedRaw as unknown as FlowGraph;
const PHASE_TREE = phaseTreeRaw as unknown as PhaseTree;
const PHASES = PHASE_TREE.phases;
const ROOT_ID = GRAPH.rootId!;
const POSITIONS = computeLayout(GRAPH, ROOT_ID);
const NODES_BY_ID = indexNodesById(GRAPH.nodes);
const EXPLORER_TREE = buildExplorerTree(GRAPH.nodes);

const NODE_RADIUS: Record<NodeType, number> = { entry: 13, call: 10, leaf: 8 };
const NODE_COLORS: Record<NodeType, { fill: string; stroke: string; label: string }> = {
  entry: { fill: "var(--node-entry-fill)", stroke: "var(--node-entry-stroke)", label: "Entry" },
  call: { fill: "var(--node-call-fill)", stroke: "var(--node-call-stroke)", label: "Call" },
  leaf: { fill: "var(--node-leaf-fill)", stroke: "var(--node-leaf-stroke)", label: "External / leaf" },
};
const NODE_BADGE_COLOR: Record<NodeType, "amber" | "teal" | "grass"> = {
  entry: "amber",
  call: "teal",
  leaf: "grass",
};
const PHASE_COLORS = ["var(--phase-1)", "var(--phase-2)", "var(--phase-3)", "var(--phase-4)", "var(--phase-5)"];

const CANVAS_BOUNDS = (() => {
  const xs = [...POSITIONS.values()].map((p) => p.x);
  const ys = [...POSITIONS.values()].map((p) => p.y);
  return {
    minX: Math.min(...xs) - 60,
    minY: Math.min(...ys) - 60,
    maxX: Math.max(...xs) + 60,
    maxY: Math.max(...ys) + 60,
  };
})();

function nodeLabel(node: FlowNode): string {
  return node.code ?? (node.calleeFullName ? shortLabel(node.calleeFullName) : node.id);
}

function getNodeIcon(type: NodeType) {
  if (type === "entry") return <Hash size={12} />;
  if (type === "leaf") return <ArrowRight size={12} />;
  return <GitBranch size={12} />;
}

function edgePath(from: NodePosition, to: NodePosition, fromR: number, toR: number): string {
  const dx = to.x - from.x;
  const dy = to.y - from.y;
  const dist = Math.sqrt(dx * dx + dy * dy) || 1;
  const nx = dx / dist;
  const ny = dy / dist;
  const ARROW = 7;
  const x1 = (from.x + fromR * nx).toFixed(1);
  const y1 = (from.y + fromR * ny).toFixed(1);
  const x2 = (to.x - (toR + ARROW) * nx).toFixed(1);
  const y2 = (to.y - (toR + ARROW) * ny).toFixed(1);
  return `M ${x1} ${y1} L ${x2} ${y2}`;
}

// ── Explorer tree ────────────────────────────────────────────────────────

function ExplorerRow({
  item,
  depth,
  selectedNodeId,
  onSelect,
}: {
  item: ExplorerItem;
  depth: number;
  selectedNodeId: string | null;
  onSelect: (id: string) => void;
}) {
  const [open, setOpen] = useState(depth < 2);
  const isBranch = item.kind !== "node";
  const isSelected = item.nodeId === selectedNodeId;
  const node = item.nodeId ? NODES_BY_ID.get(item.nodeId) : undefined;

  return (
    <Box>
      <button
        onClick={() => (isBranch ? setOpen((o) => !o) : item.nodeId && onSelect(item.nodeId))}
        style={{
          all: "unset",
          boxSizing: "border-box",
          display: "flex",
          alignItems: "center",
          gap: 6,
          width: "100%",
          padding: "5px 8px",
          paddingLeft: 8 + depth * 14,
          cursor: "pointer",
          borderRadius: 4,
          color: isSelected ? "var(--accent-11)" : "var(--gray-11)",
          background: isSelected ? "var(--accent-a3)" : "transparent",
        }}
      >
        {isBranch ? (
          open ? (
            <ChevronDown size={12} />
          ) : (
            <ChevronRight size={12} />
          )
        ) : (
          <Box style={{ width: 12, flexShrink: 0 }} />
        )}
        {item.kind === "package" && <FolderOpen size={12} color="var(--amber-9)" />}
        {item.kind === "class" && <PackageIcon size={12} color="var(--teal-9)" />}
        {item.kind === "node" && node && getNodeIcon(node.type)}
        <Text size="1" truncate style={{ fontFamily: MONO }}>
          {item.name}
        </Text>
      </button>
      {isBranch &&
        open &&
        item.children?.map((child, i) => (
          <ExplorerRow
            key={child.nodeId ?? `${child.name}-${i}`}
            item={child}
            depth={depth + 1}
            selectedNodeId={selectedNodeId}
            onSelect={onSelect}
          />
        ))}
    </Box>
  );
}

// ── Node search ──────────────────────────────────────────────────────────

function NodeSearchPanel({
  selectedNodeId,
  onSelect,
}: {
  selectedNodeId: string | null;
  onSelect: (id: string) => void;
}) {
  const [query, setQuery] = useState("");
  const q = query.trim().toLowerCase();
  const results = q
    ? GRAPH.nodes.filter(
        (n) =>
          n.calleeFullName?.toLowerCase().includes(q) ||
          n.code?.toLowerCase().includes(q) ||
          n.callerMethod?.toLowerCase().includes(q),
      )
    : GRAPH.nodes;

  return (
    <Flex direction="column" style={{ height: "100%" }}>
      <Box p="2" style={{ borderBottom: "1px solid var(--gray-a5)" }}>
        <TextField.Root
          size="1"
          placeholder="Search nodes…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        >
          <TextField.Slot>
            <Search size={13} />
          </TextField.Slot>
        </TextField.Root>
      </Box>
      <ScrollArea style={{ flex: 1 }}>
        <Flex direction="column" py="1">
          {results.length === 0 && (
            <Text size="1" color="gray" align="center" mt="4">
              No nodes found
            </Text>
          )}
          {results.map((node) => {
            const colors = NODE_COLORS[node.type];
            const isSelected = node.id === selectedNodeId;
            return (
              <Box
                key={node.id}
                onClick={() => onSelect(node.id)}
                px="3"
                py="2"
                style={{ cursor: "pointer", background: isSelected ? "var(--accent-a3)" : "transparent" }}
              >
                <Flex align="center" gap="2">
                  <Box style={{ color: colors.stroke, display: "flex" }}>{getNodeIcon(node.type)}</Box>
                  <Text size="1" truncate style={{ fontFamily: MONO, flex: 1 }}>
                    {nodeLabel(node)}
                  </Text>
                  <Badge size="1" variant="soft" color={NODE_BADGE_COLOR[node.type]}>
                    {colors.label}
                  </Badge>
                </Flex>
                {node.callerMethod && (
                  <Text
                    size="1"
                    color="gray"
                    truncate
                    style={{ fontFamily: MONO, paddingLeft: 20, display: "block" }}
                  >
                    {shortLabel(node.callerMethod)}
                  </Text>
                )}
              </Box>
            );
          })}
        </Flex>
      </ScrollArea>
    </Flex>
  );
}

// ── Detail panel ─────────────────────────────────────────────────────────

function TransitionRow({ label, transition }: { label: string; transition: Transition }) {
  const color = transition.reason === "dead-end" ? "red" : transition.reason === "gate" ? "amber" : "teal";
  return (
    <Tooltip content={TRANSITION_REASON_LABELS[transition.reason]}>
      <Flex align="center" gap="2" style={{ cursor: "default" }}>
        <Badge size="1" variant="soft" color={color}>
          {transition.reason}
        </Badge>
        <Text size="1" color="gray">
          {label}
          {transition.level ? ` · L${transition.level}` : ""}
        </Text>
      </Flex>
    </Tooltip>
  );
}

function EdgeRow({ edge, otherId }: { edge: FlowEdge; otherId: string }) {
  const node = NODES_BY_ID.get(otherId);
  if (!node) return null;
  const colors = NODE_COLORS[node.type];
  return (
    <Flex align="center" gap="2">
      <Box style={{ width: 6, height: 6, borderRadius: "50%", background: colors.stroke, flexShrink: "0" }} />
      <Text size="1" truncate style={{ fontFamily: MONO, flex: 1 }}>
        {nodeLabel(node)}
      </Text>
      <Badge size="1" variant="outline" color="gray">
        {edge.type}
      </Badge>
    </Flex>
  );
}

function DetailPanel({ node, onClose }: { node: FlowNode; onClose: () => void }) {
  const colors = NODE_COLORS[node.type];
  const incoming = incomingEdges(node.id, GRAPH.edges);
  const outgoing = outgoingEdges(node.id, GRAPH.edges);
  const phaseIdx = phaseIndexForNode(node.id, PHASES);
  const phase = phaseIdx !== null ? PHASES[phaseIdx] : null;
  const ownerClass = ownerClassOf(node);
  const file = CLASS_FILES[ownerClass];

  return (
    <ScrollArea style={{ height: "100%" }}>
      <Flex direction="column">
        <Flex
          align="center"
          justify="between"
          p="3"
          style={{
            borderBottom: "1px solid var(--gray-a5)",
            position: "sticky",
            top: "0",
            background: "var(--color-panel-solid)",
          }}
        >
          <Flex align="center" gap="2">
            <Box style={{ color: colors.stroke, display: "flex" }}>{getNodeIcon(node.type)}</Box>
            <Text
              size="1"
              weight="bold"
              style={{ color: colors.stroke, letterSpacing: "0.08em", textTransform: "uppercase" }}
            >
              {colors.label}
            </Text>
          </Flex>
          <IconButton size="1" variant="ghost" color="gray" onClick={onClose}>
            <X size={14} />
          </IconButton>
        </Flex>

        <Box p="3" style={{ borderBottom: "1px solid var(--gray-a5)" }}>
          <Heading size="3" style={{ fontFamily: MONO }}>
            {nodeLabel(node)}
          </Heading>
          {node.calleeFullName && (
            <Text
              as="p"
              size="1"
              color="gray"
              mt="2"
              style={{ fontFamily: MONO, wordBreak: "break-all" }}
            >
              {node.calleeFullName}
            </Text>
          )}
          <Flex gap="2" mt="2" wrap="wrap">
            {node.deadEnd && (
              <Badge color="red" variant="soft">
                <AlertTriangle size={11} /> dead end
              </Badge>
            )}
            {file && (
              <Badge color="gray" variant="soft" style={{ fontFamily: MONO }}>
                {file}
                {node.line ? `:${node.line}` : ""}
              </Badge>
            )}
          </Flex>
        </Box>

        {phase && (
          <Box p="3" style={{ borderBottom: "1px solid var(--gray-a5)" }}>
            <Text size="1" weight="bold" color="gray" style={{ textTransform: "uppercase", letterSpacing: "0.08em" }}>
              Phase {phaseIdx! + 1}
            </Text>
            <Flex direction="column" gap="2" mt="2">
              {phase.opened_by && <TransitionRow label="Opened by" transition={phase.opened_by} />}
              {phase.transitions.map((t, i) => (
                <TransitionRow key={i} label="Joined by" transition={t} />
              ))}
              {!phase.opened_by && phase.transitions.length === 0 && (
                <Text size="1" color="gray">
                  First phase of the trace.
                </Text>
              )}
            </Flex>
          </Box>
        )}

        {!phase && node.type === "call" && outgoing.some((e) => e.type === "invoke") && (
          <Box p="3" style={{ borderBottom: "1px solid var(--gray-a5)" }}>
            <Text size="1" color="gray">
              Not part of a single phase — its callee resolved to more than one phase (see the phases
              below).
            </Text>
          </Box>
        )}

        {incoming.length > 0 && (
          <Box p="3" style={{ borderBottom: "1px solid var(--gray-a5)" }}>
            <Text size="1" weight="bold" color="gray" style={{ textTransform: "uppercase", letterSpacing: "0.08em" }}>
              Called by ({incoming.length})
            </Text>
            <Flex direction="column" gap="1" mt="2">
              {incoming.map((e, i) => (
                <EdgeRow key={i} edge={e} otherId={e.from} />
              ))}
            </Flex>
          </Box>
        )}

        {outgoing.length > 0 && (
          <Box p="3">
            <Text size="1" weight="bold" color="gray" style={{ textTransform: "uppercase", letterSpacing: "0.08em" }}>
              Calls ({outgoing.length})
            </Text>
            <Flex direction="column" gap="1" mt="2">
              {outgoing.map((e, i) => (
                <EdgeRow key={i} edge={e} otherId={e.to} />
              ))}
            </Flex>
          </Box>
        )}
      </Flex>
    </ScrollArea>
  );
}

// ── Minimap ──────────────────────────────────────────────────────────────

function Minimap({ selectedId }: { selectedId: string | null }) {
  const W = 160;
  const H = 120;
  const { minX, minY, maxX, maxY } = CANVAS_BOUNDS;
  const scale = Math.min((W - 8) / (maxX - minX), (H - 8) / (maxY - minY));

  return (
    <Card
      size="1"
      style={{ position: "absolute", bottom: "16px", right: "16px", padding: 0, overflow: "hidden" }}
    >
      <svg width={W} height={H}>
        {PHASES.map((phase, i) => {
          const bbox = computePhaseBBox(phase, POSITIONS, 20);
          if (!bbox) return null;
          return (
            <rect
              key={i}
              x={(bbox.x - minX) * scale + 4}
              y={(bbox.y - minY) * scale + 4}
              width={bbox.width * scale}
              height={bbox.height * scale}
              rx={2}
              fill="none"
              stroke={PHASE_COLORS[i % PHASE_COLORS.length]}
              strokeWidth={0.5}
              opacity={0.6}
            />
          );
        })}
        {GRAPH.nodes.map((node) => {
          const pos = POSITIONS.get(node.id);
          if (!pos) return null;
          const colors = NODE_COLORS[node.type];
          const x = (pos.x - minX) * scale + 4;
          const y = (pos.y - minY) * scale + 4;
          return (
            <circle
              key={node.id}
              cx={x}
              cy={y}
              r={Math.max(1.2, NODE_RADIUS[node.type] * scale * 0.7)}
              fill={colors.fill}
              stroke={node.id === selectedId ? "var(--accent-9)" : colors.stroke}
              strokeWidth={node.id === selectedId ? 1.5 : 0.6}
            />
          );
        })}
      </svg>
      <Box px="2" py="1" style={{ borderTop: "1px solid var(--gray-a5)" }}>
        <Text size="1" color="gray" style={{ fontFamily: MONO }}>
          minimap
        </Text>
      </Box>
    </Card>
  );
}

// ── Main App ─────────────────────────────────────────────────────────────

type PanelTab = "explorer" | "search";

export default function App() {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [zoom, setZoom] = useState(0.85);
  const [pan, setPan] = useState({ x: 120, y: 60 });
  const [isPanning, setIsPanning] = useState(false);
  const [panStart, setPanStart] = useState({ x: 0, y: 0 });
  const [leftOpen, setLeftOpen] = useState(true);
  const [rightOpen, setRightOpen] = useState(false);
  const [activeTab, setActiveTab] = useState<PanelTab>("explorer");
  const [hoveredId, setHoveredId] = useState<string | null>(null);
  const svgRef = useRef<SVGSVGElement>(null);

  const selectedNode = selectedId ? (NODES_BY_ID.get(selectedId) ?? null) : null;

  const connectedIds = useMemo(() => {
    if (!selectedId) return null;
    const ids = new Set<string>([selectedId]);
    for (const e of GRAPH.edges) {
      if (e.from === selectedId) ids.add(e.to);
      if (e.to === selectedId) ids.add(e.from);
    }
    return ids;
  }, [selectedId]);

  const handleNodeClick = useCallback((id: string) => {
    setSelectedId((prev) => {
      const next = prev === id ? null : id;
      setRightOpen(next !== null);
      return next;
    });
  }, []);

  const handleSelectFromPanel = useCallback((id: string) => {
    setSelectedId(id);
    setRightOpen(true);
  }, []);

  const handleMouseDown = useCallback(
    (e: React.MouseEvent<SVGSVGElement>) => {
      if ((e.target as SVGElement).closest(".graph-node")) return;
      setIsPanning(true);
      setPanStart({ x: e.clientX - pan.x, y: e.clientY - pan.y });
    },
    [pan],
  );

  const handleMouseMove = useCallback(
    (e: React.MouseEvent<SVGSVGElement>) => {
      if (!isPanning) return;
      setPan({ x: e.clientX - panStart.x, y: e.clientY - panStart.y });
    },
    [isPanning, panStart],
  );

  const handleMouseUp = useCallback(() => setIsPanning(false), []);

  const handleWheel = useCallback((e: WheelEvent) => {
    e.preventDefault();
    setZoom((z) => Math.min(3, Math.max(0.2, z - e.deltaY * 0.001)));
  }, []);

  useEffect(() => {
    const el = svgRef.current;
    if (!el) return;
    el.addEventListener("wheel", handleWheel, { passive: false });
    return () => el.removeEventListener("wheel", handleWheel);
  }, [handleWheel]);

  const methodCount = GRAPH.nodes.filter((n) => n.type !== "leaf").length;

  return (
    <Flex
      direction="column"
      width="100vw"
      height="100vh"
      overflow="hidden"
      style={{ background: "var(--color-background)" }}
    >
      {/* Header */}
      <Flex
        align="center"
        justify="between"
        px="4"
        flexShrink="0"
        height="40px"
        style={{ borderBottom: "1px solid var(--gray-a5)", background: "var(--color-panel-solid)" }}
      >
        <Flex align="center" gap="3">
          <Flex align="center" gap="2">
            <Flex
              align="center"
              justify="center"
              width="20px"
              height="20px"
              style={{
                borderRadius: 4,
                background: "var(--accent-a3)",
                border: "1px solid var(--accent-a6)",
              }}
            >
              <GitBranch size={12} color="var(--accent-9)" />
            </Flex>
            <Text weight="bold" size="2">
              FlowMap
            </Text>
          </Flex>
          <Text color="gray">·</Text>
          <Text size="1" color="gray" style={{ fontFamily: MONO }}>
            java_project
          </Text>
          <Text color="gray">·</Text>
          <Badge color="grass" variant="soft" style={{ fontFamily: MONO }}>
            {GRAPH.entryPoint ? shortLabel(GRAPH.entryPoint) : "entry"}
          </Badge>
        </Flex>
        <Flex align="center" gap="2">
          <Button size="1" variant="soft" color="amber">
            <Upload size={12} /> Upload
          </Button>
          <IconButton size="1" variant="ghost" color="gray">
            <Settings size={14} />
          </IconButton>
        </Flex>
      </Flex>

      {/* Main */}
      <Flex flexGrow="1" style={{ minHeight: 0 }}>
        {/* Left panel */}
        {leftOpen && (
          <Box
            flexShrink="0"
            width="240px"
            style={{ borderRight: "1px solid var(--gray-a5)", background: "var(--color-panel-solid)" }}
          >
            <Flex direction="column" style={{ height: "100%" }}>
              <Tabs.Root
                value={activeTab}
                onValueChange={(v) => setActiveTab(v as PanelTab)}
                style={{ display: "flex", flexDirection: "column", flex: 1, minHeight: 0 }}
              >
                <Flex align="center" style={{ borderBottom: "1px solid var(--gray-a5)" }}>
                  <Tabs.List style={{ flex: 1 }}>
                    <Tabs.Trigger value="explorer">
                      <FolderOpen size={12} style={{ marginRight: 6 }} />
                      Explorer
                    </Tabs.Trigger>
                    <Tabs.Trigger value="search">
                      <Search size={12} style={{ marginRight: 6 }} />
                      Nodes
                    </Tabs.Trigger>
                  </Tabs.List>
                  <IconButton size="1" variant="ghost" color="gray" onClick={() => setLeftOpen(false)}>
                    <Minimize2 size={13} />
                  </IconButton>
                </Flex>
                <Tabs.Content value="explorer" style={{ flex: 1, minHeight: 0, overflow: "hidden" }}>
                  <ScrollArea style={{ height: "100%" }}>
                    <Box py="1">
                      {EXPLORER_TREE.map((item, i) => (
                        <ExplorerRow
                          key={item.name + i}
                          item={item}
                          depth={0}
                          selectedNodeId={selectedId}
                          onSelect={handleSelectFromPanel}
                        />
                      ))}
                    </Box>
                  </ScrollArea>
                </Tabs.Content>
                <Tabs.Content value="search" style={{ flex: 1, minHeight: 0, overflow: "hidden" }}>
                  <NodeSearchPanel selectedNodeId={selectedId} onSelect={handleSelectFromPanel} />
                </Tabs.Content>
              </Tabs.Root>

              {/* Legend */}
              <Box p="3" flexShrink="0" style={{ borderTop: "1px solid var(--gray-a5)" }}>
                <Text
                  size="1"
                  weight="bold"
                  color="gray"
                  style={{ textTransform: "uppercase", letterSpacing: "0.08em" }}
                >
                  Node types
                </Text>
                <Flex direction="column" gap="1" mt="2">
                  {(Object.entries(NODE_COLORS) as [NodeType, (typeof NODE_COLORS)[NodeType]][]).map(
                    ([type, c]) => (
                      <Flex key={type} align="center" gap="2">
                        <Box
                          style={{
                            width: 10,
                            height: 10,
                            borderRadius: "50%",
                            background: c.fill,
                            border: `1px solid ${c.stroke}`,
                            flexShrink: 0,
                          }}
                        />
                        <Text size="1" color="gray">
                          {c.label}
                        </Text>
                      </Flex>
                    ),
                  )}
                </Flex>
                <Separator size="4" my="2" />
                <Text
                  size="1"
                  weight="bold"
                  color="gray"
                  style={{ textTransform: "uppercase", letterSpacing: "0.08em" }}
                >
                  Phases
                </Text>
                <Flex direction="column" gap="1" mt="2">
                  {PHASES.map((_, i) => (
                    <Flex key={i} align="center" gap="2">
                      <Box
                        style={{
                          width: 10,
                          height: 10,
                          borderRadius: 2,
                          border: `1px solid ${PHASE_COLORS[i % PHASE_COLORS.length]}`,
                          flexShrink: 0,
                        }}
                      />
                      <Text size="1" color="gray">
                        Phase {i + 1}
                      </Text>
                    </Flex>
                  ))}
                </Flex>
              </Box>
            </Flex>
          </Box>
        )}

        {/* Graph canvas */}
        <Box
          position="relative"
          flexGrow="1"
          overflow="hidden"
          style={{ minWidth: 0, background: "var(--canvas-background)" }}
        >
          <svg style={{ position: "absolute", inset: 0, pointerEvents: "none" }} width="100%" height="100%">
            <defs>
              <pattern id="dotgrid" x="0" y="0" width="24" height="24" patternUnits="userSpaceOnUse">
                <circle cx="1" cy="1" r="0.7" fill="var(--canvas-dot)" />
              </pattern>
            </defs>
            <rect width="100%" height="100%" fill="url(#dotgrid)" />
          </svg>

          <svg
            ref={svgRef}
            style={{
              position: "absolute",
              inset: 0,
              width: "100%",
              height: "100%",
              cursor: isPanning ? "grabbing" : "grab",
            }}
            onMouseDown={handleMouseDown}
            onMouseMove={handleMouseMove}
            onMouseUp={handleMouseUp}
            onMouseLeave={handleMouseUp}
          >
            <defs>
              <marker id="arrow-sequence" markerWidth="7" markerHeight="7" refX="6" refY="3.5" orient="auto">
                <path d="M0,0 L0,7 L7,3.5 z" fill="var(--edge-sequence)" opacity="0.85" />
              </marker>
              <marker id="arrow-invoke" markerWidth="7" markerHeight="7" refX="6" refY="3.5" orient="auto">
                <path d="M0,0 L0,7 L7,3.5 z" fill="var(--edge-invoke)" opacity="0.85" />
              </marker>
              <marker id="arrow-fallback" markerWidth="7" markerHeight="7" refX="6" refY="3.5" orient="auto">
                <path d="M0,0 L0,7 L7,3.5 z" fill="var(--edge-fallback)" opacity="0.85" />
              </marker>
            </defs>

            <g transform={`translate(${pan.x}, ${pan.y}) scale(${zoom})`}>
              {/* Phase scope boxes */}
              {PHASES.map((phase, i) => {
                const bbox = computePhaseBBox(phase, POSITIONS);
                if (!bbox) return null;
                const color = PHASE_COLORS[i % PHASE_COLORS.length];
                return (
                  <g key={i}>
                    <rect
                      x={bbox.x}
                      y={bbox.y}
                      width={bbox.width}
                      height={bbox.height}
                      rx={14}
                      fill={color}
                      fillOpacity={0.04}
                      stroke={color}
                      strokeOpacity={0.45}
                      strokeWidth={1}
                      strokeDasharray="4 3"
                    >
                      <title>
                        {phase.opened_by
                          ? `Opened by: ${TRANSITION_REASON_LABELS[phase.opened_by.reason]}`
                          : "First phase of the trace"}
                      </title>
                    </rect>
                    <text x={bbox.x + 10} y={bbox.y + 16} fontSize="9" fontFamily={MONO} fill={color} opacity={0.85}>
                      phase {i + 1}
                      {phase.opened_by ? ` · ${phase.opened_by.reason}` : ""}
                    </text>
                  </g>
                );
              })}

              {/* Edges */}
              {GRAPH.edges.map((edge, i) => {
                const from = POSITIONS.get(edge.from);
                const to = POSITIONS.get(edge.to);
                const fromNode = NODES_BY_ID.get(edge.from);
                const toNode = NODES_BY_ID.get(edge.to);
                if (!from || !to || !fromNode || !toNode) return null;
                const isDimmed = connectedIds ? !connectedIds.has(edge.from) && !connectedIds.has(edge.to) : false;
                const color = edge.fallback
                  ? "var(--edge-fallback)"
                  : edge.type === "invoke"
                    ? "var(--edge-invoke)"
                    : "var(--edge-sequence)";
                const marker = edge.fallback ? "arrow-fallback" : edge.type === "invoke" ? "arrow-invoke" : "arrow-sequence";
                const dash = edge.fallback ? "2 3" : edge.type === "invoke" ? "4 3" : undefined;
                return (
                  <path
                    key={i}
                    d={edgePath(from, to, NODE_RADIUS[fromNode.type], NODE_RADIUS[toNode.type])}
                    fill="none"
                    stroke={color}
                    strokeWidth={1.2}
                    strokeDasharray={dash}
                    opacity={isDimmed ? 0.1 : 0.8}
                    markerEnd={`url(#${marker})`}
                    style={{ transition: "opacity 0.15s" }}
                  >
                    <title>
                      {edge.fallback
                        ? "Inferred fallback return edge — the callee never reached this continuation directly"
                        : edge.type}
                    </title>
                  </path>
                );
              })}

              {/* Nodes */}
              {GRAPH.nodes.map((node) => {
                const pos = POSITIONS.get(node.id);
                if (!pos) return null;
                const colors = NODE_COLORS[node.type];
                const r = NODE_RADIUS[node.type];
                const isSelected = node.id === selectedId;
                const isHovered = node.id === hoveredId;
                const isDimmed = connectedIds ? !connectedIds.has(node.id) : false;

                return (
                  <g
                    key={node.id}
                    className="graph-node"
                    style={{ cursor: "pointer", opacity: isDimmed ? 0.25 : 1, transition: "opacity 0.15s" }}
                    onClick={() => handleNodeClick(node.id)}
                    onMouseEnter={() => setHoveredId(node.id)}
                    onMouseLeave={() => setHoveredId(null)}
                  >
                    {(isSelected || isHovered) && (
                      <circle cx={pos.x} cy={pos.y} r={r + 6} fill={colors.stroke + "28"} />
                    )}
                    {node.deadEnd && (
                      <circle
                        cx={pos.x}
                        cy={pos.y}
                        r={r + 3}
                        fill="none"
                        stroke="var(--node-dead-end-stroke)"
                        strokeWidth={1}
                        strokeDasharray="2 2"
                      />
                    )}
                    {node.type === "entry" ? (
                      <rect
                        x={pos.x - r * 0.75}
                        y={pos.y - r * 0.75}
                        width={r * 1.5}
                        height={r * 1.5}
                        transform={`rotate(45 ${pos.x} ${pos.y})`}
                        fill={colors.fill}
                        stroke={colors.stroke}
                        strokeWidth={isSelected ? 2 : isHovered ? 1.5 : 1}
                      />
                    ) : (
                      <circle
                        cx={pos.x}
                        cy={pos.y}
                        r={r}
                        fill={colors.fill}
                        stroke={colors.stroke}
                        strokeWidth={isSelected ? 2 : isHovered ? 1.5 : 1}
                        strokeDasharray={node.type === "leaf" ? "3 2" : undefined}
                      />
                    )}
                    {isSelected && <circle cx={pos.x} cy={pos.y} r={r * 0.35} fill={colors.stroke} opacity={0.7} />}
                    <text
                      x={pos.x + r + 9}
                      y={pos.y}
                      dominantBaseline="middle"
                      fontSize="11"
                      fontFamily={MONO}
                      fill={isSelected ? "var(--canvas-foreground)" : isHovered ? "var(--gray-11)" : "var(--canvas-muted)"}
                      style={{ transition: "fill 0.1s", userSelect: "none" }}
                    >
                      {nodeLabel(node)}
                    </text>
                    <title>{node.calleeFullName}</title>
                  </g>
                );
              })}
            </g>
          </svg>

          {/* Zoom controls */}
          <Card
            size="1"
            style={{
              position: "absolute",
              bottom: "16px",
              left: "50%",
              transform: "translateX(-50%)",
              padding: 6,
            }}
          >
            <Flex align="center" gap="1">
              <IconButton size="1" variant="ghost" color="gray" onClick={() => setZoom((z) => Math.max(0.2, z - 0.15))}>
                <ZoomOut size={14} />
              </IconButton>
              <Text size="1" color="gray" style={{ width: 44, textAlign: "center", fontFamily: MONO }}>
                {Math.round(zoom * 100)}%
              </Text>
              <IconButton size="1" variant="ghost" color="gray" onClick={() => setZoom((z) => Math.min(3, z + 0.15))}>
                <ZoomIn size={14} />
              </IconButton>
              <Separator orientation="vertical" size="1" mx="1" />
              <IconButton
                size="1"
                variant="ghost"
                color="gray"
                onClick={() => {
                  setZoom(0.85);
                  setPan({ x: 120, y: 60 });
                }}
              >
                <Maximize2 size={14} />
              </IconButton>
            </Flex>
          </Card>

          {!leftOpen && (
            <IconButton
              size="1"
              variant="surface"
              color="gray"
              onClick={() => setLeftOpen(true)}
              style={{ position: "absolute", top: "12px", left: "12px" }}
            >
              <ChevronRight size={14} />
            </IconButton>
          )}

          <Minimap selectedId={selectedId} />
        </Box>

        {/* Right detail panel */}
        {rightOpen && selectedNode && (
          <Box
            flexShrink="0"
            width="288px"
            style={{ borderLeft: "1px solid var(--gray-a5)", background: "var(--color-panel-solid)" }}
          >
            <DetailPanel
              node={selectedNode}
              onClose={() => {
                setRightOpen(false);
                setSelectedId(null);
              }}
            />
          </Box>
        )}
      </Flex>

      {/* Status bar */}
      <Flex
        align="center"
        gap="3"
        px="4"
        flexShrink="0"
        height="24px"
        style={{ borderTop: "1px solid var(--gray-a5)", background: "var(--color-panel-solid)" }}
      >
        <Flex align="center" gap="2">
          <Box style={{ width: 6, height: 6, borderRadius: "50%", background: "var(--grass-9)" }} />
          <Text size="1" color="gray">
            flattened control flow
          </Text>
        </Flex>
        <Text size="1" color="gray">
          ·
        </Text>
        <Text size="1" color="gray" style={{ fontFamily: MONO }}>
          {methodCount} methods
        </Text>
        <Text size="1" color="gray">
          ·
        </Text>
        <Text size="1" color="gray" style={{ fontFamily: MONO }}>
          {GRAPH.edges.length} edges
        </Text>
        <Text size="1" color="gray">
          ·
        </Text>
        <Text size="1" color="gray" style={{ fontFamily: MONO }}>
          {PHASES.length} phases
        </Text>
        <Flex align="center" gap="2" ml="auto">
          {selectedNode && (
            <>
              <Text size="1" color="amber" style={{ fontFamily: MONO }}>
                {nodeLabel(selectedNode)}
              </Text>
              <Text size="1" color="gray">
                ·
              </Text>
            </>
          )}
          <Text size="1" color="gray" style={{ fontFamily: MONO }}>
            zoom {Math.round(zoom * 100)}%
          </Text>
        </Flex>
      </Flex>
    </Flex>
  );
}
