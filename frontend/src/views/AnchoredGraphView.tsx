import { createContext, useContext, useState, useRef, useCallback, useEffect, useMemo } from "react";
import {
  Box,
  Flex,
  Text,
  Heading,
  Badge,
  IconButton,
  TextField,
  ScrollArea,
  Separator,
  Tabs,
  Tooltip,
  Card,
  Slider,
  Dialog,
  Button,
} from "@radix-ui/themes";
import {
  ChevronRight,
  ChevronDown,
  ZoomIn,
  ZoomOut,
  Maximize2,
  X,
  GitBranch,
  Minimize2,
  Search,
  FolderOpen,
  FileCode2,
  Package as PackageIcon,
  Hash,
  ArrowRight,
  AlertTriangle,
  Repeat2,
  RotateCcw,
  Info,
} from "lucide-react";

import { CLASS_FILES } from "../data/classFiles";
import {
  ANCHORED_VISUALISATION,
  FULL_GRAPH,
  OPSEQ_VISUALISATIONS,
  graphVisualisation,
  methodParticipatesInOpseq,
  opseqChoicesForMethod,
  type GraphVisualisation,
  type OpseqChoice,
} from "../data/graph";
import type { FlowNode, FlowEdge, LoopGroup, NodeType, Transition } from "../types/flowmap";
import { computeLayout, type NodePosition, type RowGap } from "../lib/layout";
import {
  defaultSelection,
  buildBranchPanels,
  flowEdgeKey,
  visibleGraphSelection,
  type BranchPanel,
  type BranchSelection,
  activeBranchPanels,
  panelRouteTargetIds,
} from "../lib/branches";
import { computePanelGeometry } from "../lib/panelGeometry";
import { BranchRegions, BranchSwitchers } from "../components/BranchOverlay";
import { MONO } from "../lib/ui";
import {
  shortLabel,
  ownerClassOf,
  phaseIndexForNode,
  incomingEdges,
  outgoingEdges,
  computePhaseBBox,
  TRANSITION_REASON_LABELS,
  buildExplorerTree,
  buildProjectExplorerTree,
  parsedSourceFileForClass,
  sourcePathForClass,
  type ExplorerItem,
  type ProjectExplorerItem,
} from "../lib/graph";

const NODE_RADIUS: Record<NodeType, number> = { entry: 13, call: 10, leaf: 8 };
const NODE_COLORS: Record<NodeType, { fill: string; stroke: string; label: string }> = {
  entry: { fill: "var(--node-entry-fill)", stroke: "var(--node-entry-stroke)", label: "Entry" },
  call: { fill: "var(--node-call-fill)", stroke: "var(--node-call-stroke)", label: "Call" },
  leaf: { fill: "var(--node-leaf-fill)", stroke: "var(--node-leaf-stroke)", label: "External / leaf" },
};
const PHASE_COLORS = ["var(--phase-1)", "var(--phase-2)", "var(--phase-3)", "var(--phase-4)", "var(--phase-5)"];

interface GraphViewData extends GraphVisualisation {
  rootId: string;
  nodesById: Map<string, FlowNode>;
  explorerTree: ExplorerItem[];
  projectTree: ProjectExplorerItem[];
  panels: BranchPanel[];
  flowEdges: FlowEdge[];
  loopsById: Map<string, LoopGroup>;
  focusMethodFullName?: string;
  onSelectProjectMethod: (methodFullName: string) => void;
  treeOpenOverrides: Map<string, boolean>;
  onToggleTreePath: (path: string, defaultOpen: boolean) => void;
}

function makeGraphViewData(
  visualisation: GraphVisualisation,
  onSelectProjectMethod: (methodFullName: string) => void,
  treeOpenOverrides: Map<string, boolean>,
  onToggleTreePath: (path: string, defaultOpen: boolean) => void,
  focusMethodFullName?: string,
): GraphViewData {
  const { graph, phaseTree } = visualisation;
  const rootId = graph.rootId;
  if (!rootId) throw new Error("A flattened graph must have a rootId");
  const nodesById = new Map(graph.nodes.map((node) => [node.id, node]));
  const explorerTree = buildExplorerTree(graph.nodes);
  const projectTree = buildProjectExplorerTree(FULL_GRAPH.nodes, CLASS_FILES);
  const panels = buildBranchPanels(graph, rootId);
  const loopsById = new Map((graph.loopGroups ?? []).map((loop) => [loop.id, loop]));
  return {
    graph, phaseTree, rootId, nodesById, explorerTree, projectTree, panels, loopsById,
    focusMethodFullName, onSelectProjectMethod, treeOpenOverrides, onToggleTreePath,
    flowEdges: graph.edges.filter((edge) => edge.type !== "data" && !edge.loopBack),
  };
}

const GraphViewContext = createContext<GraphViewData | null>(null);

function useGraphViewData(): GraphViewData {
  const data = useContext(GraphViewContext);
  if (!data) throw new Error("AnchoredGraphView must be rendered inside GraphViewContext");
  return data;
}

function nodeLabel(node: FlowNode): string {
  return node.code ?? (node.calleeFullName ? shortLabel(node.calleeFullName) : node.id);
}

// Canvas labels are drawn to the right of their node at a fixed column
// width, so a long call expression runs straight through the next column.
// The full text is still on the node's <title>.
const LABEL_MAX = 30;
function canvasLabel(node: FlowNode): string {
  const label = nodeLabel(node);
  return label.length > LABEL_MAX ? `${label.slice(0, LABEL_MAX - 1)}…` : label;
}

function getNodeIcon(type: NodeType) {
  if (type === "entry") return <Hash size={12} />;
  if (type === "leaf") return <ArrowRight size={12} />;
  return <GitBranch size={12} />;
}

// `type: "sequence"` in the data is three different things, and painting
// them alike is what made the flow look wrong: a RETURN edge runs from a
// callee's tail back to the caller's continuation, so it always moves left
// (measured: 34 of 34 return edges go to a shallower depth), and drawn as a
// plain forward step it reads as an unexplained jump backwards. "data" is
// not control flow at all and was also inheriting the sequence colour.
type EdgeClass = "sequence" | "invoke" | "return" | "fallback";

const EDGE_STYLE: Record<EdgeClass, { color: string; dash?: string; label: string }> = {
  sequence: { color: "var(--edge-sequence)", label: "next statement" },
  invoke: { color: "var(--edge-invoke)", dash: "4 3", label: "calls into" },
  return: { color: "var(--edge-return)", dash: "6 3", label: "returns to caller" },
  fallback: {
    color: "var(--edge-fallback)",
    dash: "2 3",
    label: "inferred fallback return — the callee never reached this continuation directly",
  },
};

// Callers must pass FLOW_EDGES, never GRAPH.edges: "data" has no class here.
function classifyEdge(edge: FlowEdge): EdgeClass {
  if (edge.type === "invoke") return "invoke";
  if (edge.fallback) return "fallback";
  return edge.returnFrom ? "return" : "sequence";
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
  path,
  selectedNodeId,
  onSelect,
}: {
  item: ExplorerItem;
  depth: number;
  path: string;
  selectedNodeId: string | null;
  onSelect: (id: string) => void;
}) {
  const { nodesById, treeOpenOverrides, onToggleTreePath } = useGraphViewData();
  const isBranch = item.kind !== "node";
  const defaultOpen = depth < 2;
  const open = treeOpenOverrides.get(`operation:${path}`) ?? defaultOpen;
  const isSelected = item.nodeId === selectedNodeId;
  const node = item.nodeId ? nodesById.get(item.nodeId) : undefined;

  return (
    <Box>
      <button
        onClick={() => (
          isBranch
            ? onToggleTreePath(`operation:${path}`, defaultOpen)
            : item.nodeId && onSelect(item.nodeId)
        )}
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
            path={`${path}/${child.kind}:${child.name}`}
            selectedNodeId={selectedNodeId}
            onSelect={onSelect}
          />
        ))}
    </Box>
  );
}

function ProjectExplorerRow({
  item,
  depth,
  path,
  selectedNodeId,
  forceOpen = false,
  onSelectProjectMethod: onSelectOverride,
  treeOpenOverrides: openOverridesOverride,
  onToggleTreePath: onToggleOverride,
}: {
  item: ProjectExplorerItem;
  depth: number;
  path: string;
  selectedNodeId: string | null;
  forceOpen?: boolean;
  onSelectProjectMethod?: (methodFullName: string) => void;
  treeOpenOverrides?: Map<string, boolean>;
  onToggleTreePath?: (path: string, defaultOpen: boolean) => void;
}) {
  const data = useContext(GraphViewContext);
  const graph = data?.graph ?? FULL_GRAPH;
  const onSelectProjectMethod = onSelectOverride ?? data?.onSelectProjectMethod;
  const treeOpenOverrides = openOverridesOverride ?? data?.treeOpenOverrides ?? new Map();
  const onToggleTreePath = onToggleOverride ?? data?.onToggleTreePath;
  const isBranch = item.kind !== "method";
  const storedOpen = treeOpenOverrides.get(`project:${path}`) ?? true;
  const isOpen = forceOpen || storedOpen;
  const participatesInOpseq = item.methodFullName
    ? methodParticipatesInOpseq(item.methodFullName)
    : true;
  const activeNode = item.methodFullName
    ? graph.nodes.find((node) => node.type === "entry" && node.calleeFullName === item.methodFullName)
    : undefined;
  const isSelected = activeNode?.id === selectedNodeId;

  return (
    <Box>
      <button
        onClick={() => {
          if (isBranch) onToggleTreePath?.(`project:${path}`, true);
          else if (item.methodFullName) onSelectProjectMethod?.(item.methodFullName);
        }}
        title={item.methodFullName
          ? `${item.methodFullName}${participatesInOpseq ? "" : " — Not part of an operation sequence"}`
          : item.name}
        aria-label={item.methodFullName
          ? `${item.name}${participatesInOpseq ? "" : ", not part of an operation sequence"}`
          : item.name}
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
          color: isSelected
            ? "var(--accent-11)"
            : participatesInOpseq ? "var(--gray-11)" : "var(--gray-8)",
          background: isSelected ? "var(--accent-a3)" : "transparent",
          opacity: participatesInOpseq ? 1 : 0.55,
        }}
      >
        {isBranch ? (
          isOpen ? <ChevronDown size={12} /> : <ChevronRight size={12} />
        ) : (
          <Box style={{ width: 12, flexShrink: 0 }} />
        )}
        {item.kind === "folder" && <FolderOpen size={12} color="var(--amber-9)" />}
        {item.kind === "file" && <FileCode2 size={12} color="var(--teal-9)" />}
        {item.kind === "method" && (
          <Hash size={12} color={participatesInOpseq ? "var(--gray-9)" : "var(--gray-7)"} />
        )}
        <Text size="1" truncate style={{ fontFamily: MONO }}>
          {item.name}
        </Text>
      </button>
      {isBranch && isOpen && item.children?.map((child, index) => (
        <ProjectExplorerRow
          key={`${child.kind}-${child.name}-${index}`}
          item={child}
          depth={depth + 1}
          path={`${path}/${child.kind}:${child.name}`}
          selectedNodeId={selectedNodeId}
          forceOpen={forceOpen}
          onSelectProjectMethod={onSelectOverride}
          treeOpenOverrides={openOverridesOverride}
          onToggleTreePath={onToggleOverride}
        />
      ))}
    </Box>
  );
}

function filterProjectTree(items: ProjectExplorerItem[], query: string): ProjectExplorerItem[] {
  const normalizedQuery = query.trim().toLowerCase();
  if (!normalizedQuery) return items;

  return items.flatMap((item) => {
    const selfMatches = item.name.toLowerCase().includes(normalizedQuery)
      || item.methodFullName?.toLowerCase().includes(normalizedQuery);
    if (selfMatches) return [item];

    const children = filterProjectTree(item.children ?? [], normalizedQuery);
    return children.length > 0 ? [{ ...item, children }] : [];
  });
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
  const { nodesById } = useGraphViewData();
  const node = nodesById.get(otherId);
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
  const { flowEdges, phaseTree } = useGraphViewData();
  const phases = phaseTree.phases;
  const colors = NODE_COLORS[node.type];
  const incoming = incomingEdges(node.id, flowEdges);
  const outgoing = outgoingEdges(node.id, flowEdges);
  const phaseIdx = phaseIndexForNode(node.id, phases);
  const phase = phaseIdx !== null ? phases[phaseIdx] : null;
  const ownerClass = ownerClassOf(node);
  const parsedSourceFile = node.sourceFile && !node.sourceFile.startsWith("<")
    ? node.sourceFile
    : parsedSourceFileForClass(FULL_GRAPH.nodes, ownerClass);
  const file = parsedSourceFile ?? (
    node.type !== "leaf" && ownerClass !== "(unknown)"
      ? sourcePathForClass(ownerClass, CLASS_FILES)
      : undefined
  );

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

const NODE_EXPLANATIONS: Record<NodeType, string> = {
  entry: "Starts execution inside a method.",
  call: "Invokes another operation from this method.",
  leaf: "Ends at an external or unresolved operation.",
};

const EDGE_LEGEND_LABEL: Record<EdgeClass, string> = {
  sequence: "seq",
  invoke: "invoke",
  return: "return",
  fallback: "fallback",
};

function LegendNodeShape({ type }: { type: NodeType }) {
  const colors = NODE_COLORS[type];
  return (
    <svg width="22" height="22" viewBox="0 0 22 22" style={{ flexShrink: 0 }} aria-hidden="true">
      {type === "entry" ? (
        <rect
          x="5"
          y="5"
          width="12"
          height="12"
          transform="rotate(45 11 11)"
          fill={colors.fill}
          stroke={colors.stroke}
        />
      ) : (
        <circle
          cx="11"
          cy="11"
          r={type === "call" ? 7 : 6}
          fill={colors.fill}
          stroke={colors.stroke}
          strokeDasharray={type === "leaf" ? "3 2" : undefined}
        />
      )}
    </svg>
  );
}

function GraphLegend() {
  return (
    <Card size="2" style={{ width: 330, maxHeight: "calc(100vh - 150px)", overflowY: "auto" }}>
      <Text size="1" weight="bold" color="gray" style={{ textTransform: "uppercase", letterSpacing: "0.08em" }}>
        Node types
      </Text>
      <Flex direction="column" gap="2" mt="2">
        {(Object.keys(NODE_COLORS) as NodeType[]).map((type) => (
          <Flex key={type} align="center" gap="2">
            <LegendNodeShape type={type} />
            <Box>
              <Text size="1" weight="bold" as="div">{NODE_COLORS[type].label}</Text>
              <Text size="1" color="gray" as="div">{NODE_EXPLANATIONS[type]}</Text>
            </Box>
          </Flex>
        ))}
      </Flex>

      <Separator size="4" my="3" />
      <Text size="1" weight="bold" color="gray" style={{ textTransform: "uppercase", letterSpacing: "0.08em" }}>
        Edge types
      </Text>
      <Flex direction="column" gap="2" mt="2">
        {(Object.keys(EDGE_STYLE) as EdgeClass[]).map((kind) => (
          <Flex key={kind} align="center" gap="2">
            <svg width="28" height="10" style={{ flexShrink: 0 }} aria-hidden="true">
              <line
                x1="1"
                y1="5"
                x2="26"
                y2="5"
                stroke={EDGE_STYLE[kind].color}
                strokeWidth="1.5"
                strokeDasharray={EDGE_STYLE[kind].dash}
              />
            </svg>
            <Box>
              <Text size="1" weight="bold" as="div">{EDGE_LEGEND_LABEL[kind]}</Text>
              <Text size="1" color="gray" as="div">{EDGE_STYLE[kind].label}</Text>
            </Box>
          </Flex>
        ))}
      </Flex>

      <Separator size="4" my="3" />
      <Text size="1" weight="bold" color="gray" style={{ textTransform: "uppercase", letterSpacing: "0.08em" }}>
        Icons
      </Text>
      <Flex direction="column" gap="2" mt="2">
        <Flex align="center" gap="2">
          <Repeat2 size={16} color="var(--orange-9)" style={{ flexShrink: 0, margin: 3 }} />
          <Box>
            <Text size="1" weight="bold" as="div">Loop</Text>
            <Text size="1" color="gray" as="div">Executes inside a source-code loop.</Text>
          </Box>
        </Flex>
        <Flex align="center" gap="2">
          <RotateCcw size={16} color="var(--purple-9)" style={{ flexShrink: 0, margin: 3 }} />
          <Box>
            <Text size="1" weight="bold" as="div">Recurse</Text>
            <Text size="1" color="gray" as="div">Calls the current method recursively.</Text>
          </Box>
        </Flex>
      </Flex>
    </Card>
  );
}

// ── Main App ─────────────────────────────────────────────────────────────

// ── Anchored graph view ──────────────────────────────────────────────────
// The trace itself: project explorer / operation tree on the left,
// the flattened CFG on the canvas, a node detail panel on the right. Owns
// its own status bar, since every number in it counts something that only
// exists in this view.

type PanelTab = "explore" | "operation";
type ResizeSide = "left" | "right";

const LEFT_PANEL_MIN = 180;
const LEFT_PANEL_MAX = 560;
const RIGHT_PANEL_MIN = 240;
const RIGHT_PANEL_MAX = 600;

function clampPanelWidth(width: number, side: ResizeSide): number {
  const min = side === "left" ? LEFT_PANEL_MIN : RIGHT_PANEL_MIN;
  const absoluteMax = side === "left" ? LEFT_PANEL_MAX : RIGHT_PANEL_MAX;
  const responsiveMax = typeof window === "undefined" ? absoluteMax : window.innerWidth * 0.45;
  return Math.round(Math.min(Math.max(width, min), Math.max(min, Math.min(absoluteMax, responsiveMax))));
}

function PanelResizeHandle({
  side,
  active,
  onMouseDown,
  onKeyboardResize,
}: {
  side: ResizeSide;
  active: boolean;
  onMouseDown: (event: React.MouseEvent<HTMLDivElement>) => void;
  onKeyboardResize: (delta: number) => void;
}) {
  return (
    <Box
      role="separator"
      aria-label={`Resize ${side} panel`}
      aria-orientation="vertical"
      tabIndex={0}
      onMouseDown={onMouseDown}
      onKeyDown={(event) => {
        if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
        event.preventDefault();
        const screenDelta = event.key === "ArrowRight" ? 16 : -16;
        onKeyboardResize(side === "left" ? screenDelta : -screenDelta);
      }}
      style={{
        width: 6,
        flexShrink: 0,
        cursor: "col-resize",
        background: active ? "var(--accent-a5)" : "var(--gray-a3)",
        borderLeft: `1px solid ${active ? "var(--accent-a7)" : "var(--gray-a5)"}`,
        borderRight: `1px solid ${active ? "var(--accent-a7)" : "var(--gray-a5)"}`,
        outline: "none",
      }}
    />
  );
}

function GraphCanvasView({ initialTab = "explore" }: { initialTab?: PanelTab }) {
  const {
    graph: GRAPH,
    phaseTree,
    rootId: ROOT_ID,
    nodesById: NODES_BY_ID,
    explorerTree: EXPLORER_TREE,
    projectTree: PROJECT_TREE,
    panels: PANELS,
    flowEdges: FLOW_EDGES,
    loopsById: LOOPS_BY_ID,
    focusMethodFullName,
  } = useGraphViewData();
  const PHASES = phaseTree.phases;
  const focusNodeId = focusMethodFullName
    ? GRAPH.nodes.find((node) => node.type === "entry" && node.calleeFullName === focusMethodFullName)?.id ?? null
    : null;
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [armSelection, setArmSelection] = useState<BranchSelection>(() => {
    const selection = defaultSelection(PANELS);
    if (!focusNodeId) return selection;
    for (const panel of PANELS) {
      const containingArm = panel.arms.find((arm) => arm.memberIds.includes(focusNodeId));
      if (containingArm) selection.set(panel.id, containingArm.id);
    }
    return selection;
  });
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 120, y: 60 });
  const [isPanning, setIsPanning] = useState(false);
  const [panStart, setPanStart] = useState({ x: 0, y: 0 });
  const [leftOpen, setLeftOpen] = useState(true);
  const [rightOpen, setRightOpen] = useState(false);
  const [leftWidth, setLeftWidth] = useState(240);
  const [rightWidth, setRightWidth] = useState(288);
  const [resizingSide, setResizingSide] = useState<ResizeSide | null>(null);
  const resizeStartRef = useRef<{ side: ResizeSide; x: number; width: number } | null>(null);
  const [activeTab, setActiveTab] = useState<PanelTab>(initialTab);
  const [exploreQuery, setExploreQuery] = useState("");
  const [legendOpen, setLegendOpen] = useState(false);
  const [hoveredId, setHoveredId] = useState<string | null>(null);
  // Branch panels are the default overlay: they are what the graph is for.
  // Phases answer a different question and would fight them for the same
  // screen space, so only one is shown at a time.
  const [overlay, setOverlay] = useState<"branches" | "phases" | "none">("branches");
  const [hoveredPanelId, setHoveredPanelId] = useState<string | null>(null);
  const svgRef = useRef<SVGSVGElement>(null);

  const selectedNode = selectedId ? (NODES_BY_ID.get(selectedId) ?? null) : null;
  const filteredProjectTree = useMemo(
    () => filterProjectTree(PROJECT_TREE, exploreQuery),
    [PROJECT_TREE, exploreQuery],
  );

  // Everything downstream of the arm selection: which nodes exist, where
  // they sit, and how big the canvas is. Rows are ranked over the VISIBLE
  // subgraph, so a collapsed arm leaves no gap behind.
  const visibleSelection = useMemo(
    () => visibleGraphSelection(GRAPH, PANELS, armSelection),
    [GRAPH, PANELS, armSelection],
  );
  const visibleIds = visibleSelection.nodeIds;
  const visibleEdges = useMemo(
    () => FLOW_EDGES.filter((edge) => visibleSelection.edgeIds.has(flowEdgeKey(edge))),
    [FLOW_EDGES, visibleSelection],
  );
  const activePanels = useMemo(() => {
    const nestedActive = activeBranchPanels(PANELS, armSelection);
    return nestedActive.filter((panel) => {
      const selectedId = armSelection.get(panel.id) ?? panel.defaultArmId;
      const arm = panel.arms.find((candidate) => candidate.id === selectedId);
      if (!arm) return false;

      // A selected non-empty arm proves the panel is reachable only when at
      // least one of its members survived the reachability walk. For an
      // empty arm, its visible exit plays the same role. This also hides a
      // later guard that shares an anchor with an earlier selected throw.
      if (!arm.empty) return arm.memberIds.some((id) => visibleIds.has(id));
      const targets = arm.exitTargetIds.length > 0
        ? arm.exitTargetIds
        : (panel.convergesAt ? [panel.convergesAt] : []);
      if (targets.some((id) => visibleIds.has(id))) return true;
      return panel.branchPointIds.some((id) => visibleIds.has(id));
    });
  }, [PANELS, armSelection, visibleIds]);

  // Each visible panel's selected arm becomes a row band, so two branches
  // that are not nested in each other never share a row -- otherwise their
  // rectangles overlap however tightly they are drawn (see separateBands).
  const rowBands = useMemo(
    () =>
      activePanels.flatMap((panel) => {
        const selected = armSelection.get(panel.id) ?? panel.defaultArmId;
        const arm = panel.arms.find((a) => a.id === selected);
        const memberIds = (arm?.memberIds ?? []).filter((id) => visibleIds.has(id));
        return memberIds.length > 0 ? [{ id: panel.id, memberIds }] : [];
      }),
    [activePanels, armSelection, visibleIds],
  );

  // An empty arm has no member node for row-banding to reserve. Without an
  // explicit gap, its visual box is painted over the immediate continuation.
  // Consecutive stripped conditions can share that same edge, so reservations
  // are cumulative: the arrow gets one row per compact panel. When it enters
  // another panel's first member, reserve one more row for that panel's header.
  const branchBoundaryGaps = useMemo<RowGap[]>(
    () => {
      const gaps: RowGap[] = [];
      const emptyRoutes = new Map<string, { fromId: string; toId: string; count: number }>();
      const selectedPanelHeadIds = new Set<string>();
      const outgoing = new Map<string, FlowEdge[]>();
      for (const edge of visibleEdges) {
        const edges = outgoing.get(edge.from);
        if (edges) edges.push(edge);
        else outgoing.set(edge.from, [edge]);
      }

      // Nodes executed inside a call site before its synthesized return edge.
      // TRY attaches to the try tail (often an internal call), so its empty
      // noCatch panel must wait until this complete subtree has finished.
      const invokedSubtreeIds = (panel: BranchPanel, toId: string): string[] => {
        const points = new Set(panel.branchPointIds);
        const stack = visibleEdges
          .filter((edge) => edge.type === "invoke" && points.has(edge.from))
          .map((edge) => edge.to);
        const found = new Set<string>();
        while (stack.length > 0) {
          const id = stack.pop()!;
          if (id === toId || found.has(id)) continue;
          found.add(id);
          for (const edge of outgoing.get(id) ?? []) {
            if (edge.to === toId) continue;
            if (edge.returnFrom != null && points.has(edge.returnFrom)) continue;
            stack.push(edge.to);
          }
        }
        return [...found];
      };

      for (const panel of activePanels) {
        const selected = armSelection.get(panel.id) ?? panel.defaultArmId;
        const arm = panel.arms.find((candidate) => candidate.id === selected);
        if (!arm || arm.empty) continue;
        if (arm.headId && visibleIds.has(arm.headId)) selectedPanelHeadIds.add(arm.headId);
      }

      for (const panel of activePanels) {
        const selected = armSelection.get(panel.id) ?? panel.defaultArmId;
        const arm = panel.arms.find((candidate) => candidate.id === selected);
        const fromId = panel.branchPointIds[0];
        if (!arm) continue;

        if (arm.empty) {
          const targetIds = panelRouteTargetIds(panel, activePanels, armSelection);
          const routeEdge = visibleEdges.find((edge) =>
            targetIds.includes(edge.to)
            && edge.type === (panel.structure === "DISPATCH" ? "invoke" : "sequence")
            && (panel.branchPointIds.includes(edge.from)
              || (edge.returnFrom != null && panel.branchPointIds.includes(edge.returnFrom))),
          );
          const toId = routeEdge?.to ?? targetIds.find((id) => visibleIds.has(id));
          if (!fromId || !toId || !visibleIds.has(fromId)) continue;
          const layoutFromId = routeEdge?.from ?? fromId;
          if (panel.switcherPosition === "after") {
            gaps.push(...invokedSubtreeIds(panel, toId).map((memberId) => ({
              fromId: memberId,
              toId,
              rows: 1,
            })));
          }
          const key = `${layoutFromId}\u0000${toId}`;
          const route = emptyRoutes.get(key);
          if (route) route.count++;
          else emptyRoutes.set(key, { fromId: layoutFromId, toId, count: 1 });
          continue;
        }

        // Route targets are entry nodes for non-empty arms. They must not be
        // reused as the arm's continuation: doing so creates backwards row-gap
        // constraints (especially for throw arms) and stretches every pass.
        if (arm.terminus === "throw") continue;
        const toId = arm.exitTargetId ?? panel.convergesAt;
        if (!toId || !visibleIds.has(toId)) continue;

        // A rectangle around an arm must end before its continuation starts.
        // The flattened graph only guarantees every continuation is below its
        // *direct* predecessor; a deeply inlined sibling can otherwise extend
        // the arm's box below that continuation. Make the continuation follow
        // every node the arm owns, so it cannot render inside the region.
        gaps.push(...arm.memberIds
          .filter((memberId) => visibleIds.has(memberId) && memberId !== toId)
          .map((memberId) => ({ fromId: memberId, toId, rows: 1 })));
      }

      for (const route of emptyRoutes.values()) {
        gaps.push({
          fromId: route.fromId,
          toId: route.toId,
          rows: route.count + (
            route.count > 1 || selectedPanelHeadIds.has(route.toId) ? 1 : 0
          ),
        });
      }
      return gaps;
    },
    [activePanels, armSelection, visibleIds, visibleEdges],
  );

  const positions = useMemo(
    () => computeLayout(
      { ...GRAPH, edges: visibleEdges },
      ROOT_ID,
      visibleIds,
      rowBands,
      branchBoundaryGaps,
    ),
    [GRAPH, ROOT_ID, visibleIds, visibleEdges, rowBands, branchBoundaryGaps],
  );
  const panelLabelWidths = useMemo(
    () => new Map(
      GRAPH.nodes.map((node) => [
        node.id,
        NODE_RADIUS[node.type] + 9 + canvasLabel(node).length * 6.6,
      ]),
    ),
    [GRAPH],
  );
  const panelGeometry = useMemo(
    () => computePanelGeometry(
      activePanels,
      armSelection,
      positions,
      panelLabelWidths,
      visibleEdges,
    ),
    [activePanels, armSelection, positions, panelLabelWidths, visibleEdges],
  );

  const handleArmSelect = useCallback((panelId: string, armId: string) => {
    setArmSelection((prev) => {
      const next = new Map(prev);
      next.set(panelId, armId);
      return next;
    });
  }, []);

  const connectedIds = useMemo(() => {
    if (!selectedId) return null;
    const ids = new Set<string>([selectedId]);
    for (const e of visibleEdges) {
      if (e.from === selectedId) ids.add(e.to);
      if (e.to === selectedId) ids.add(e.from);
    }
    return ids;
  }, [selectedId, visibleEdges]);

  const handleNodeClick = useCallback((id: string) => {
    setSelectedId((prev) => {
      const next = prev === id ? null : id;
      setRightOpen(next !== null);
      return next;
    });
  }, []);

  const handleSelectFromPanel = useCallback((id: string) => {
    setSelectedId((previousId) => {
      const nextId = previousId === id ? null : id;
      setRightOpen(nextId !== null);
      return nextId;
    });
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

  const beginPanelResize = useCallback((side: ResizeSide, event: React.MouseEvent<HTMLDivElement>) => {
    event.preventDefault();
    resizeStartRef.current = {
      side,
      x: event.clientX,
      width: side === "left" ? leftWidth : rightWidth,
    };
    setResizingSide(side);
  }, [leftWidth, rightWidth]);

  useEffect(() => {
    if (!resizingSide) return;
    const previousUserSelect = document.body.style.userSelect;
    const previousCursor = document.body.style.cursor;
    document.body.style.userSelect = "none";
    document.body.style.cursor = "col-resize";

    const handleResize = (event: MouseEvent) => {
      const start = resizeStartRef.current;
      if (!start) return;
      const pointerDelta = event.clientX - start.x;
      const nextWidth = start.width + (start.side === "left" ? pointerDelta : -pointerDelta);
      if (start.side === "left") setLeftWidth(clampPanelWidth(nextWidth, "left"));
      else setRightWidth(clampPanelWidth(nextWidth, "right"));
    };
    const finishResize = () => {
      resizeStartRef.current = null;
      setResizingSide(null);
    };

    window.addEventListener("mousemove", handleResize);
    window.addEventListener("mouseup", finishResize);
    return () => {
      window.removeEventListener("mousemove", handleResize);
      window.removeEventListener("mouseup", finishResize);
      document.body.style.userSelect = previousUserSelect;
      document.body.style.cursor = previousCursor;
    };
  }, [resizingSide]);

  const handleWheel = useCallback((e: WheelEvent) => {
    e.preventDefault();

    // Trackpad pinch is exposed as ctrl+wheel by browsers. Zoom around the
    // pointer so the item under the fingers stays in place.
    if (e.ctrlKey || e.metaKey) {
      const rect = svgRef.current?.getBoundingClientRect();
      if (!rect) return;
      const pointerX = e.clientX - rect.left;
      const pointerY = e.clientY - rect.top;
      const nextZoom = Math.min(3, Math.max(0.2, zoom * Math.exp(-e.deltaY * 0.01)));
      const graphX = (pointerX - pan.x) / zoom;
      const graphY = (pointerY - pan.y) / zoom;
      setPan({
        x: pointerX - graphX * nextZoom,
        y: pointerY - graphY * nextZoom,
      });
      setZoom(nextZoom);
      return;
    }

    // Ordinary wheel/two-finger scrolling pans the canvas. Shift+wheel is
    // horizontal for mouse users; trackpads already provide deltaX.
    const shiftHorizontal = e.shiftKey && e.deltaX === 0;
    setPan((current) => ({
      x: current.x - (shiftHorizontal ? e.deltaY : e.deltaX),
      y: current.y - (shiftHorizontal ? 0 : e.deltaY),
    }));
  }, [pan, zoom]);

  useEffect(() => {
    const el = svgRef.current;
    if (!el) return;
    el.addEventListener("wheel", handleWheel, { passive: false });
    return () => el.removeEventListener("wheel", handleWheel);
  }, [handleWheel]);

  const methodCount = GRAPH.nodes.filter(
    (n) => n.type !== "leaf" && visibleIds.has(n.id),
  ).length;
  const conditionalCount = PANELS.filter((p) => p.kind === "conditional").length;


  return (
    <Flex direction="column" width="100%" height="100%" overflow="hidden" style={{ minHeight: 0 }}>
      {/* Main */}
      <Flex flexGrow="1" style={{ minHeight: 0 }}>
        {/* Left panel */}
        {leftOpen && (
          <>
            <Box
              flexShrink="0"
              width={`${leftWidth}px`}
              style={{ background: "var(--color-panel-solid)" }}
            >
              <Flex direction="column" style={{ height: "100%" }}>
              <Tabs.Root
                value={activeTab}
                onValueChange={(v) => setActiveTab(v as PanelTab)}
                style={{ display: "flex", flexDirection: "column", flex: 1, minHeight: 0 }}
              >
                <Flex align="center" style={{ borderBottom: "1px solid var(--gray-a5)" }}>
                  <Tabs.List style={{ flex: 1 }}>
                    <Tabs.Trigger value="explore">
                      <FolderOpen size={12} style={{ marginRight: 6 }} />
                      Explore
                    </Tabs.Trigger>
                    <Tabs.Trigger value="operation">
                      <GitBranch size={12} style={{ marginRight: 6 }} />
                      Operation
                    </Tabs.Trigger>
                  </Tabs.List>
                  <IconButton size="1" variant="ghost" color="gray" onClick={() => setLeftOpen(false)}>
                    <Minimize2 size={13} />
                  </IconButton>
                </Flex>
                <Tabs.Content value="explore" style={{ flex: 1, minHeight: 0, overflow: "hidden" }}>
                  <Flex direction="column" style={{ height: "100%" }}>
                    <Box p="2" style={{ borderBottom: "1px solid var(--gray-a5)" }}>
                      <TextField.Root
                        size="1"
                        placeholder="Search project…"
                        value={exploreQuery}
                        onChange={(event) => setExploreQuery(event.target.value)}
                        aria-label="Search project files and methods"
                      >
                        <TextField.Slot>
                          <Search size={13} />
                        </TextField.Slot>
                      </TextField.Root>
                    </Box>
                    <ScrollArea style={{ flex: 1 }}>
                      <Box py="1">
                        {filteredProjectTree.length === 0 && (
                          <Text size="1" color="gray" align="center" as="p" mt="4">
                            No project items found
                          </Text>
                        )}
                        {filteredProjectTree.map((item, i) => (
                          <ProjectExplorerRow
                            key={`${item.kind}-${item.name}-${i}`}
                            item={item}
                            depth={0}
                            path={`${item.kind}:${item.name}`}
                            selectedNodeId={selectedId}
                            forceOpen={exploreQuery.trim().length > 0}
                          />
                        ))}
                      </Box>
                    </ScrollArea>
                  </Flex>
                </Tabs.Content>
                <Tabs.Content value="operation" style={{ flex: 1, minHeight: 0, overflow: "hidden" }}>
                  <ScrollArea style={{ height: "100%" }}>
                    <Box py="1">
                      {EXPLORER_TREE.map((item, i) => (
                        <ExplorerRow
                          key={item.name + i}
                          item={item}
                          depth={0}
                          path={`${item.kind}:${item.name}`}
                          selectedNodeId={selectedId}
                          onSelect={handleSelectFromPanel}
                        />
                      ))}
                    </Box>
                  </ScrollArea>
                </Tabs.Content>
              </Tabs.Root>
              </Flex>
            </Box>
            <PanelResizeHandle
              side="left"
              active={resizingSide === "left"}
              onMouseDown={(event) => beginPanelResize("left", event)}
              onKeyboardResize={(delta) => setLeftWidth((width) => clampPanelWidth(width + delta, "left"))}
            />
          </>
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
              {(Object.keys(EDGE_STYLE) as EdgeClass[]).map((kind) => (
                <marker
                  key={kind}
                  id={`arrow-${kind}`}
                  markerWidth="7"
                  markerHeight="7"
                  refX="6"
                  refY="3.5"
                  orient="auto"
                >
                  <path d="M0,0 L0,7 L7,3.5 z" fill={EDGE_STYLE[kind].color} opacity="0.85" />
                </marker>
              ))}
            </defs>

            <g transform={`translate(${pan.x}, ${pan.y}) scale(${zoom})`}>
              {/* Branch regions go behind everything: they are the ground
                  the nodes sit on, not an annotation over them. */}
              {overlay === "branches" && (
                <BranchRegions geometries={panelGeometry} activeId={hoveredPanelId} />
              )}

              {/* Phase scope boxes */}
              {overlay === "phases" && PHASES.map((phase, i) => {
                const bbox = computePhaseBBox(phase, positions);
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
              {visibleEdges.map((edge, i) => {
                const from = positions.get(edge.from);
                const to = positions.get(edge.to);
                const fromNode = NODES_BY_ID.get(edge.from);
                const toNode = NODES_BY_ID.get(edge.to);
                if (!from || !to || !fromNode || !toNode) return null;
                const isDimmed = connectedIds ? !connectedIds.has(edge.from) && !connectedIds.has(edge.to) : false;
                const kind = classifyEdge(edge);
                const style = EDGE_STYLE[kind];
                return (
                  <path
                    key={i}
                    d={edgePath(from, to, NODE_RADIUS[fromNode.type], NODE_RADIUS[toNode.type])}
                    fill="none"
                    stroke={style.color}
                    strokeWidth={1.2}
                    strokeDasharray={style.dash}
                    opacity={isDimmed ? 0.1 : 0.8}
                    markerEnd={`url(#arrow-${kind})`}
                    style={{ transition: "opacity 0.15s" }}
                  >
                    <title>{style.label}</title>
                  </path>
                );
              })}

              {/* Nodes */}
              {GRAPH.nodes.map((node) => {
                const pos = positions.get(node.id);
                if (!pos) return null;
                const colors = NODE_COLORS[node.type];
                const r = NODE_RADIUS[node.type];
                const isSelected = node.id === selectedId;
                const isHovered = node.id === hoveredId;
                const isDimmed = connectedIds ? !connectedIds.has(node.id) : false;
                const loopLabels = (node.loopIds ?? []).map((loopId) => {
                  const loop = LOOPS_BY_ID.get(loopId);
                  if (!loop) return "loop";
                  const kind = loop.kind.toLowerCase().replace("_", " ");
                  return loop.conditionCode ? `${kind}: ${loop.conditionCode}` : kind;
                });

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
                    {loopLabels.length > 0 && (
                      <g pointerEvents="none">
                        <title>{`Inside ${loopLabels.join("; ")}`}</title>
                        <Repeat2
                          x={pos.x - r - 18}
                          y={pos.y - 6}
                          width={12}
                          height={12}
                          color="var(--orange-9)"
                          strokeWidth={1.8}
                        />
                      </g>
                    )}
                    {node.recursive && (
                      <g pointerEvents="none">
                        <title>Recursive call</title>
                        <RotateCcw
                          x={pos.x - r - 18 - (loopLabels.length > 0 ? 16 : 0)}
                          y={pos.y - 6}
                          width={12}
                          height={12}
                          color="var(--purple-9)"
                          strokeWidth={1.8}
                        />
                      </g>
                    )}
                    <text
                      x={pos.x + r + 9}
                      y={pos.y}
                      dominantBaseline="middle"
                      fontSize="11"
                      fontFamily={MONO}
                      fontWeight={isSelected ? 700 : 400}
                      fill="var(--canvas-foreground)"
                      opacity={isSelected || isHovered ? 1 : 0.82}
                      style={{ transition: "fill 0.1s, opacity 0.1s", userSelect: "none" }}
                    >
                      {canvasLabel(node)}
                    </text>
                    <title>{nodeLabel(node)}</title>
                  </g>
                );
              })}

              {/* Switcher pills last, so they stay clickable above the
                  nodes and edges the region contains. */}
              {overlay === "branches" && (
                <BranchSwitchers
                  geometries={panelGeometry}
                  selection={armSelection}
                  activeId={hoveredPanelId}
                  onSelect={handleArmSelect}
                  onHover={setHoveredPanelId}
                />
              )}
            </g>
          </svg>

          <Flex
            direction="column"
            align="end"
            gap="2"
            style={{ position: "absolute", bottom: 64, right: 16 }}
          >
            {legendOpen && <GraphLegend />}
            <Tooltip content={legendOpen ? "Hide legend" : "Show legend"}>
              <IconButton
                aria-label={legendOpen ? "Hide graph legend" : "Show graph legend"}
                aria-expanded={legendOpen}
                size="2"
                variant="surface"
                color="gray"
                onClick={() => setLegendOpen((open) => !open)}
              >
                <Info size={15} />
              </IconButton>
            </Tooltip>
          </Flex>

          {/* Zoom controls */}
          <Card
            size="1"
            style={{
              position: "absolute",
              bottom: "16px",
              right: "16px",
              padding: 6,
            }}
          >
            <Flex align="center" gap="1">
              <IconButton aria-label="Zoom out" size="1" variant="ghost" color="gray" onClick={() => setZoom((z) => Math.max(0.2, z - 0.15))}>
                <ZoomOut size={14} />
              </IconButton>
              <Text size="1" color="gray" style={{ width: 44, textAlign: "center", fontFamily: MONO }}>
                {Math.round(zoom * 100)}%
              </Text>
              <Slider
                size="1"
                min={20}
                max={300}
                step={5}
                value={[zoom * 100]}
                onValueChange={([value]) => setZoom(value / 100)}
                aria-label="Graph zoom"
                style={{ width: 88 }}
              />
              <IconButton aria-label="Zoom in" size="1" variant="ghost" color="gray" onClick={() => setZoom((z) => Math.min(3, z + 0.15))}>
                <ZoomIn size={14} />
              </IconButton>
              <Separator orientation="vertical" size="1" mx="1" />
              <IconButton
                aria-label="Reset graph view"
                size="1"
                variant="ghost"
                color="gray"
                onClick={() => {
                  setZoom(1);
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
        </Box>

        {/* Right detail panel */}
        {rightOpen && selectedNode && (
          <>
            <PanelResizeHandle
              side="right"
              active={resizingSide === "right"}
              onMouseDown={(event) => beginPanelResize("right", event)}
              onKeyboardResize={(delta) => setRightWidth((width) => clampPanelWidth(width + delta, "right"))}
            />
            <Box
              flexShrink="0"
              width={`${rightWidth}px`}
              style={{ background: "var(--color-panel-solid)" }}
            >
              <DetailPanel
                node={selectedNode}
                onClose={() => {
                  setRightOpen(false);
                  setSelectedId(null);
                }}
              />
            </Box>
          </>
        )}
      </Flex>
      {/* Status bar */}
      <Flex
        align="center"
        gap="3"
        px="4"
        flexShrink="0"
        height="24px"
        style={{
          borderTop: "1px solid var(--gray-a5)",
          background: "var(--color-panel-solid)",
          overflowX: "auto",
          whiteSpace: "nowrap",
        }}
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
          {visibleEdges.length} edges
        </Text>
        <Text size="1" color="gray">
          ·
        </Text>
        <Text size="1" color="gray" style={{ fontFamily: MONO }}>
          {conditionalCount} conditional / {PANELS.length - conditionalCount} dispatch
        </Text>
        <Text size="1" color="gray">
          ·
        </Text>
        <Text size="1" color="gray" style={{ fontFamily: MONO }}>
          {PHASES.length} phases
        </Text>
        <Flex align="center" gap="1" ml="auto">
          <Text size="1" color="gray">
            overlay
          </Text>
          {(["branches", "phases", "none"] as const).map((mode) => (
            <button
              key={mode}
              onClick={() => setOverlay(mode)}
              style={{
                all: "unset",
                cursor: "pointer",
                padding: "0 6px",
                borderRadius: 3,
                fontFamily: MONO,
                fontSize: 11,
                color: overlay === mode ? "var(--accent-11)" : "var(--gray-10)",
                background: overlay === mode ? "var(--accent-a3)" : "transparent",
              }}
            >
              {mode}
            </button>
          ))}
          <Separator orientation="vertical" size="1" mx="2" />
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

interface PendingOpseqSelection {
  methodFullName: string;
  choices: OpseqChoice[];
}

interface AnchoredGraphViewProps extends Partial<GraphVisualisation> {
  onEntryPointChange?: (entryPoint: string | undefined) => void;
  onOpseqChange?: (choice: OpseqChoice) => void;
  initialPanelTab?: PanelTab;
}

function EmptyAnchoredSelection({
  onSelectProjectMethod,
  treeOpenOverrides,
  onToggleTreePath,
}: {
  onSelectProjectMethod: (methodFullName: string) => void;
  treeOpenOverrides: Map<string, boolean>;
  onToggleTreePath: (path: string, defaultOpen: boolean) => void;
}) {
  const [query, setQuery] = useState("");
  const [leftWidth, setLeftWidth] = useState(240);
  const [resizing, setResizing] = useState(false);
  const resizeStartRef = useRef<{ x: number; width: number } | null>(null);
  const projectTree = useMemo(
    () => buildProjectExplorerTree(FULL_GRAPH.nodes, CLASS_FILES),
    [],
  );
  const filteredTree = useMemo(
    () => filterProjectTree(projectTree, query),
    [projectTree, query],
  );

  const beginResize = useCallback((event: React.MouseEvent<HTMLDivElement>) => {
    event.preventDefault();
    resizeStartRef.current = { x: event.clientX, width: leftWidth };
    setResizing(true);
  }, [leftWidth]);

  useEffect(() => {
    if (!resizing) return;
    const previousUserSelect = document.body.style.userSelect;
    const previousCursor = document.body.style.cursor;
    document.body.style.userSelect = "none";
    document.body.style.cursor = "col-resize";

    const handleResize = (event: MouseEvent) => {
      const start = resizeStartRef.current;
      if (!start) return;
      setLeftWidth(clampPanelWidth(start.width + event.clientX - start.x, "left"));
    };
    const finishResize = () => {
      resizeStartRef.current = null;
      setResizing(false);
    };

    window.addEventListener("mousemove", handleResize);
    window.addEventListener("mouseup", finishResize);
    return () => {
      window.removeEventListener("mousemove", handleResize);
      window.removeEventListener("mouseup", finishResize);
      document.body.style.userSelect = previousUserSelect;
      document.body.style.cursor = previousCursor;
    };
  }, [resizing]);

  return (
    <Flex width="100%" height="100%" style={{ minHeight: 0 }}>
      <Box
        flexShrink="0"
        width={`${leftWidth}px`}
        style={{
          background: "var(--color-panel-solid)",
        }}
      >
        <Flex direction="column" height="100%">
          <Flex
            align="center"
            px="3"
            flexShrink="0"
            height="36px"
            style={{ borderBottom: "1px solid var(--gray-a5)" }}
          >
            <FolderOpen size={12} style={{ marginRight: 6 }} />
            <Text size="1" weight="medium">Explore</Text>
          </Flex>
          <Box p="2" style={{ borderBottom: "1px solid var(--gray-a5)" }}>
            <TextField.Root
              size="1"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search project…"
              aria-label="Search project files and methods"
            >
              <TextField.Slot><Search size={12} /></TextField.Slot>
            </TextField.Root>
          </Box>
          <ScrollArea style={{ flex: 1, minHeight: 0 }}>
            <Box p="1">
              {filteredTree.map((item, index) => (
                <ProjectExplorerRow
                  key={`${item.kind}-${item.name}-${index}`}
                  item={item}
                  depth={0}
                  path={`${item.kind}:${item.name}`}
                  selectedNodeId={null}
                  forceOpen={query.trim().length > 0}
                  onSelectProjectMethod={onSelectProjectMethod}
                  treeOpenOverrides={treeOpenOverrides}
                  onToggleTreePath={onToggleTreePath}
                />
              ))}
              {filteredTree.length === 0 && (
                <Text size="1" color="gray" as="p" align="center" mt="3">
                  No matching methods.
                </Text>
              )}
            </Box>
          </ScrollArea>
        </Flex>
      </Box>
      <PanelResizeHandle
        side="left"
        active={resizing}
        onMouseDown={beginResize}
        onKeyboardResize={(delta) => (
          setLeftWidth((width) => clampPanelWidth(width + delta, "left"))
        )}
      />

      <Flex
        align="center"
        justify="center"
        flexGrow="1"
        p="5"
        style={{ minWidth: 0, background: "var(--canvas-background)" }}
      >
        <Box style={{ textAlign: "center" }}>
          <Text size="2" weight="medium">Choose an operation sequence</Text>
          <Text as="p" size="1" color="gray" mt="2">
            Select a method in Explore to open one of its stored operation contexts.
          </Text>
        </Box>
      </Flex>
    </Flex>
  );
}

export default function AnchoredGraphView(props: AnchoredGraphViewProps = {}) {
  const [treeOpenOverrides, setTreeOpenOverrides] = useState<Map<string, boolean>>(
    () => new Map(),
  );
  const handleToggleTreePath = useCallback((path: string, defaultOpen: boolean) => {
    setTreeOpenOverrides((previous) => {
      const next = new Map(previous);
      next.set(path, !(previous.get(path) ?? defaultOpen));
      return next;
    });
  }, []);
  const baseVisualisation = useMemo<GraphVisualisation>(() => ({
    ...graphVisualisation(
      props.graph ?? ANCHORED_VISUALISATION.graph,
      props.phaseTree ?? ANCHORED_VISUALISATION.phaseTree,
    ),
    rootMethodFullName: props.rootMethodFullName,
    memberMethodFullNames: props.memberMethodFullNames,
  }), [props.graph, props.phaseTree, props.rootMethodFullName, props.memberMethodFullNames]);
  const [override, setOverride] = useState<{
    baseGraph: GraphVisualisation["graph"];
    opseqId: string;
    focusMethodFullName: string;
  } | null>(null);
  const [pendingSelection, setPendingSelection] = useState<PendingOpseqSelection | null>(null);

  const currentOverride = override?.baseGraph === baseVisualisation.graph ? override : null;
  const visualisation = currentOverride
    ? OPSEQ_VISUALISATIONS[currentOverride.opseqId] ?? baseVisualisation
    : baseVisualisation;
  const focusMethodFullName = currentOverride?.focusMethodFullName;

  useEffect(() => {
    props.onEntryPointChange?.(visualisation.graph.entryPoint);
  }, [props.onEntryPointChange, visualisation.graph.entryPoint]);

  const activateChoice = useCallback((choice: OpseqChoice, methodFullName: string) => {
    setOverride({
      baseGraph: baseVisualisation.graph,
      opseqId: choice.id,
      focusMethodFullName: methodFullName,
    });
    props.onOpseqChange?.(choice);
    setPendingSelection(null);
  }, [baseVisualisation.graph, props.onOpseqChange]);

  const handleSelectProjectMethod = useCallback((methodFullName: string) => {
    const choices = opseqChoicesForMethod(methodFullName);
    if (choices.length === 1) {
      activateChoice(choices[0], methodFullName);
      return;
    }
    setPendingSelection({ methodFullName, choices });
  }, [activateChoice]);

  const data = useMemo(
    () => visualisation.graph.rootId
      ? makeGraphViewData(
        visualisation,
        handleSelectProjectMethod,
        treeOpenOverrides,
        handleToggleTreePath,
        focusMethodFullName,
      )
      : null,
    [
      visualisation,
      handleSelectProjectMethod,
      treeOpenOverrides,
      handleToggleTreePath,
      focusMethodFullName,
    ],
  );
  const graphViewKey = `${visualisation.graph.rootId ?? visualisation.graph.entryPoint ?? "empty"}:${focusMethodFullName ?? ""}`;

  return (
    <>
      {data ? (
        <GraphViewContext.Provider value={data}>
          <GraphCanvasView key={graphViewKey} initialTab={props.initialPanelTab} />
        </GraphViewContext.Provider>
      ) : (
        <EmptyAnchoredSelection
          onSelectProjectMethod={handleSelectProjectMethod}
          treeOpenOverrides={treeOpenOverrides}
          onToggleTreePath={handleToggleTreePath}
        />
      )}

      <Dialog.Root
        open={pendingSelection !== null}
        onOpenChange={(open) => {
          if (!open) setPendingSelection(null);
        }}
      >
        <Dialog.Content maxWidth="480px">
          <Dialog.Title>
            {pendingSelection?.choices.length === 0 ? "No operation sequence" : "Choose operation context"}
          </Dialog.Title>
          <Dialog.Description size="2" color="gray">
            {pendingSelection?.choices.length === 0
              ? `${shortLabel(pendingSelection.methodFullName)} is not part of a stored operation sequence.`
              : `${shortLabel(pendingSelection?.methodFullName ?? "")} appears in ${pendingSelection?.choices.length ?? 0} operations. Select the root context to display.`}
          </Dialog.Description>

          {pendingSelection && pendingSelection.choices.length > 0 && (
            <Flex direction="column" gap="2" mt="4">
              {pendingSelection.choices.map((choice) => (
                <button
                  key={choice.id}
                  onClick={() => activateChoice(choice, pendingSelection.methodFullName)}
                  style={{
                    all: "unset",
                    boxSizing: "border-box",
                    cursor: "pointer",
                    padding: "10px 12px",
                    borderRadius: 6,
                    border: "1px solid var(--gray-a6)",
                    background: "var(--gray-a2)",
                  }}
                >
                  <Text size="2" weight="medium" as="div">{choice.label}</Text>
                  <Text size="1" color="gray" as="div" mt="1" style={{ fontFamily: MONO }}>
                    {shortLabel(choice.rootMethodFullName)}
                  </Text>
                </button>
              ))}
            </Flex>
          )}

          <Flex justify="end" mt="4">
            <Dialog.Close>
              <Button variant="soft" color="gray">Cancel</Button>
            </Dialog.Close>
          </Flex>
        </Dialog.Content>
      </Dialog.Root>
    </>
  );
}
