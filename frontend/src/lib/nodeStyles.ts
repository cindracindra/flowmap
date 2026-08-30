import type { FlowNode, MethodExitKind, NodeType, TransferKind } from "../types/flowmap";

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
  structure: {
    label: "Structure anchor",
    explanation: "Structural entry, exit, or decision anchor hidden by the filtered graph view.",
    fill: "transparent",
    stroke: "transparent",
    radius: 0,
    shape: "circle",
  },
  transfer: {
    label: "Control transfer",
    explanation: "Transfers control to a containing structure exit.",
    fill: "var(--gray-4)",
    stroke: "var(--gray-9)",
    radius: 8,
    shape: "circle",
  },
};

export const NODE_TYPES: NodeType[] = ["entry", "call", "leaf", "exit", "transfer"];

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

export const TRANSFER_NODE_STYLES: Record<TransferKind, NodeVisualStyle> = {
  break: {
    ...NODE_STYLES.transfer,
    label: "Break",
    fill: "var(--gray-4)",
    stroke: "var(--gray-9)",
  },
  continue: {
    ...NODE_STYLES.transfer,
    label: "Continue",
    fill: "var(--gray-4)",
    stroke: "var(--gray-9)",
  },
};

export function nodeVisualStyle(node: FlowNode): NodeVisualStyle {
  if (node.type === "exit" && node.exitKind) return EXIT_NODE_STYLES[node.exitKind];
  if (node.type === "transfer" && node.transferKind) {
    return TRANSFER_NODE_STYLES[node.transferKind];
  }
  return NODE_STYLES[node.type];
}

export type EdgeClass = "sequence" | "invoke";

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
};
