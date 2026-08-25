import type { FlowNode, MethodExitKind, NodeType } from "../types/flowmap";

export interface NodeVisualStyle {
  label: string;
  explanation: string;
  fill: string;
  stroke: string;
  radius: number;
  shape: "circle" | "diamond";
  strokeDasharray?: string;
}

/** Canonical node styling shared by every graph canvas and legend. */
export const NODE_STYLES: Record<NodeType, NodeVisualStyle> = {
  call: {
    label: "Call",
    explanation: "Invokes another operation from this method.",
    fill: "var(--node-call-fill)",
    stroke: "var(--node-call-stroke)",
    radius: 10,
    shape: "circle",
  },
  entry: {
    label: "Entry",
    explanation: "Starts execution inside a method.",
    fill: "var(--node-entry-fill)",
    stroke: "var(--node-entry-stroke)",
    radius: 13,
    shape: "diamond",
  },
  leaf: {
    label: "External / leaf",
    explanation: "Ends at an external or unresolved operation.",
    fill: "var(--node-leaf-fill)",
    stroke: "var(--node-leaf-stroke)",
    radius: 8,
    shape: "circle",
    strokeDasharray: "3 2",
  },
  exit: {
    label: "Method exit",
    explanation: "Marks a method-local return, fallthrough, or throw.",
    fill: "var(--gray-4)",
    stroke: "var(--gray-9)",
    radius: 8,
    shape: "circle",
  },
};

export const NODE_TYPES = Object.keys(NODE_STYLES) as NodeType[];

export const EXIT_NODE_STYLES: Record<MethodExitKind, NodeVisualStyle> = {
  return: {
    ...NODE_STYLES.exit,
    label: "Explicit return",
    fill: "var(--gray-4)",
    stroke: "var(--gray-9)",
  },
  fallthrough: {
    ...NODE_STYLES.exit,
    label: "Implicit return",
    fill: "var(--gray-4)",
    stroke: "var(--gray-9)",
  },
  throw: {
    ...NODE_STYLES.exit,
    label: "Dead end",
    fill: "var(--gray-4)",
    stroke: "var(--gray-9)",
  },
};

export function nodeVisualStyle(node: FlowNode): NodeVisualStyle {
  return node.type === "exit" && node.exitKind
    ? EXIT_NODE_STYLES[node.exitKind]
    : NODE_STYLES[node.type];
}

export type EdgeClass = "sequence" | "invoke" | "return" | "fallback";

export interface EdgeVisualStyle {
  color: string;
  label: string;
  dash?: string;
}

export const EDGE_ARROW_SIZE = 6;

/** Canonical edge styling shared by graph canvases and the legend. */
export const EDGE_STYLES: Record<EdgeClass, EdgeVisualStyle> = {
  sequence: { color: "var(--edge-sequence)", label: "next statement" },
  invoke: { color: "var(--edge-invoke)", dash: "4 3", label: "calls into" },
  return: { color: "var(--edge-return)", dash: "6 3", label: "returns to caller" },
  fallback: {
    color: "var(--edge-fallback)",
    dash: "2 3",
    label: "inferred fallback return — the callee never reached this continuation directly",
  },
};
