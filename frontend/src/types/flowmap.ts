// Mirrors backend/src/flowmap/model/{node,edge,graph,phase,branch}.py field-for-field.

export type NodeType = "entry" | "call" | "leaf";

// Matches full_cfg.sc's classifyTerminus / model/node.py's Terminus.
// "throw" is the only value that's a proven non-return -- it's the sole
// source of `deadEnd`. "return"/"fallthrough" both mean normal completion.
// "continues" is a defensive value on FlowNode.terminus, not an expected
// one -- see full_cfg.sc's classifyTerminus docstring.
export type Terminus = "throw" | "return" | "fallthrough" | "continues";

export interface FlowNode {
  id: string;
  type: NodeType;
  calleeFullName?: string;
  callerMethod?: string;
  code?: string;
  line?: number;
  reason?: string;
  origId?: string;
  deadEnd?: boolean;
  // call only, extraction stage: set only when this call's own forward
  // walk found no further call. See Terminus's comment for why only
  // "throw" should be treated as a hard stop in the UI.
  terminus?: Terminus;
  // call only, extraction stage: set on EVERY call inside a branch arm
  // (not just its first) -- cross-references BranchGroup.id /
  // BranchArm.label below. A node's own membership in an arm is always a
  // direct fact here, never something to infer from graph position.
  branchGroupId?: string;
  armLabel?: string;
  // flatten stage: 0-1 BFS depth computed server-side against the
  // pre-flatten filtered graph (sequence=0, invoke=1) and stamped onto
  // each flattened node by origId -- see cfg_pipeline.py's
  // `_compute_depths`/`flatten_cfg`. Prefer this over recomputing depth
  // client-side: a fallback/returnFrom-tagged "sequence" edge has no
  // edge-type-based cost that gives the right answer for what crossing
  // it means (DESIGN.md §8's "Sixth bug"/"Seventh bug", and the
  // 2026-08-10 session-log entry, §0).
  depth?: number;
}

export type EdgeType = "sequence" | "invoke" | "data";

export interface FlowEdge {
  from: string;
  to: string;
  type: EdgeType;
  returnFrom?: string;
  fallback?: boolean;
}

// Mirrors model/branch.py. One entry per IF/TRY control structure found
// during extraction; SWITCH/FOR/WHILE/DO aren't split into arms yet.
export interface BranchArm {
  label: string;
  // Absent when `empty` is true. Every call in this arm carries this
  // group/arm on its own FlowNode.branchGroupId/armLabel -- firstCallId
  // is only which one to anchor the panel's initial highlight on. After
  // flattening, match via FlowNode.origId, not FlowNode.id -- flattening
  // clones nodes per call site, and a single branch group can correspond
  // to multiple flattened instances if its method is inlined more than
  // once.
  firstCallId?: string;
  // No further detail recorded for an empty arm -- the panel only needs
  // to know there's nothing operationally significant in it, not how it
  // technically ends (throw/return/fallthrough/continues).
  empty: boolean;
}

export interface BranchGroup {
  id: string;
  kind: string; // "IF" | "TRY" today
  conditionCode?: string;
  line?: number;
  arms: BranchArm[];
}

export interface FlowGraph {
  entryPoint?: string;
  nodes: FlowNode[];
  edges: FlowEdge[];
  rootId?: string;
  roots?: string[];
  orphans?: string[];
  branchGroups?: BranchGroup[];
}

export type TransitionReason =
  | "gate"
  | "data-related"
  | "same-callee-related"
  | "data-unrelated"
  | "class-related"
  | "class-unrelated"
  | "dead-end";

export interface Transition {
  subject?: string;
  reason: TransitionReason;
  level: 0 | 1 | 2 | 3;
}

export interface Phase {
  nodes: string[];
  opened_by: Transition | null;
  transitions: Transition[];
}

export interface PhaseTree {
  entryPoint: string;
  phases: Phase[];
}
