// Mirrors backend/src/flowmap/model/{node,edge,graph,phase,branch}.py field-for-field.

export type NodeType = "entry" | "call" | "leaf" | "exit";

export type MethodExitKind = "return" | "throw" | "fallthrough";

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
  sourceFile?: string;
  // Entry only: generated default constructor, not declared in source.
  implicitConstructor?: boolean;
  reason?: string;
  origId?: string;
  deadEnd?: boolean;
  // call only, extraction stage: set only when this call's own forward
  // walk found no further call. See Terminus's comment for why only
  // "throw" should be treated as a hard stop in the UI.
  terminus?: Terminus;
  // Filtered graph only: structural method-local completion marker. The
  // flattened graph consumes these nodes while resolving caller resumes.
  exitKind?: MethodExitKind;
  // call only, extraction stage: every (group, arm) this call belongs to,
  // set on EVERY call inside a branch arm, not just its first. A node's
  // membership is always a direct fact here, never something to infer
  // from graph position. Absent (not []) when the call is in no arm.
  //
  // A LIST because a call inside an `if` inside a `try` is in both arms
  // at once -- test membership, never read [0].
  branchArms?: BranchArmRef[];
  // Flatten-stage, instance-scoped source loops whose body contains this
  // node. Multiple ids mean nested loops.
  loopIds?: string[];
  // Flatten stage: this call was left as a cutoff because following it
  // would enter the same method recursively.
  recursive?: boolean;
  // flatten stage: the invoke-nesting level this CLONE was created at,
  // stamped server-side as the flattener builds it -- see
  // cfg_pipeline.py's `clone`. Every "invoke" edge therefore satisfies
  // target.depth === source.depth + 1, by construction. Prefer this over
  // recomputing depth client-side: a fallback/returnFrom-tagged
  // "sequence" edge has no edge-type-based cost that gives the right
  // answer for what crossing it means (DESIGN.md §8's "Sixth bug"/
  // "Seventh bug", and the 2026-08-10 session-log entry, §0).
  depth?: number;
}

export type EdgeType = "sequence" | "invoke" | "data";

export interface FlowEdge {
  from: string;
  to: string;
  type: EdgeType;
  returnFrom?: string;
  fallback?: boolean;
  // A dominance-defined loop repetition edge. Retained as CFG metadata,
  // but omitted from the linear process route and layout.
  loopBack?: boolean;
  // Flatten stage: branch selections required for this edge to execute.
  // TRY normal completion names its explicit empty `noCatch` arm.
  branchRequirements?: BranchRequirement[];
}

export interface BranchRequirement {
  groupId: string;
  armLabel: string;
}

// Mirrors model/branch.py's BranchArmRef -- one (group, arm) membership
// carried on a node.
export interface BranchArmRef {
  groupId: string;
  armLabel: string;
}

// Matches full_cfg.sc's armTerminus / model/branch.py's ArmTerminus. No
// "fallthrough": that's node-level only (FlowNode.terminus). An arm that
// runs off its own end and rejoins the flow is "continues".
export type ArmTerminus = "throw" | "return" | "continues";

// Mirrors model/branch.py. One entry per IF/TRY control structure found
// during extraction. Loops are repetition regions in LoopGroup rather than
// mutually-exclusive branch arms; SWITCH is not split into arms yet.
export interface BranchArm {
  label: string;
  // The arm's head: its first call in CFG order, recomputed server-side
  // against the surviving nodes (the extraction value is AST order, which
  // picks the wrong node whenever a call is nested inside another
  // expression). Absent when `empty`.
  //
  // Match it against FlowNode.id directly, at every stage. On a flattened
  // graph this is already the CLONE's id, because flattening scopes each
  // group to the instance it belongs to (`cs20` -> `cs20~7`) and rewrites
  // the ids inside it. Do NOT match on origId: a method inlined at two
  // call sites has two copies of this branch sharing one origId, so it
  // matches both.
  firstCallId?: string;
  // No surviving call in this arm. Recomputed post-filter, so it means
  // "nothing operationally significant left", not "no calls in the
  // source" -- pair with `terminus` to tell a skip from a stop.
  empty: boolean;
  // How this arm exits -- present for EVERY arm, empty ones included.
  // This is what separates an empty arm the panel can offer as a real
  // "skip to X" ("continues") from one that never rejoins the flow at all
  // ("return"/"throw"), which look identical post-filter: both leave
  // zero nodes behind.
  terminus?: ArmTerminus;
  // Path-level authoritative outcomes. Older artifacts may omit this and
  // retain only `terminus`/`targetIds`.
  exits?: ArmExit[];
  // The `if`/`else if` condition selecting this arm -- per ARM, not per
  // group, because an else-if chain is one group with a different
  // condition per arm. Absent on `else` and on every TRY arm.
  conditionCode?: string;
  // TRY catch arms only: declared caught type, including a multi-catch
  // union when Joern exposes it as one type name.
  exceptionType?: string;
  // Flatten stage: visible destinations after this arm exits. Empty for a
  // throw or when normal flow leaves the visible trace; caller continuation
  // nodes for return; the normal continuation for continues.
  targetIds?: string[];
}

export interface ArmExit {
  kind: "return" | "throw" | "continues";
  frontierIds?: string[];
  targetIds?: string[];
  branchRequirements?: BranchRequirement[];
}

export interface BranchGroup {
  // Extraction id (`cs20`) before flattening; instance-scoped
  // (`cs20~7`) after, since a method inlined at three call sites puts
  // three copies of the same branch in the trace. Every node id held
  // inside the group is re-pointed at that instance's clones to match.
  id: string;
  kind: string; // "IF" | "TRY" today
  // Full name of the method this control structure lives in.
  method?: string;
  line?: number;
  arms: BranchArm[];
  // Where the fork hangs off, i.e. the node(s) to attach the panel to (a
  // group is not itself a node). Computed against the filtered graph.
  // Absent when no arm has a surviving head to work back from -- such a
  // group has nothing to render.
  //
  // A LIST because a TRY forks at the END of its try body, once per tail:
  // `try { a(); if (x) b(); else c(); } catch ...` can divert to the
  // handler from both b() and c(). An IF always has exactly one.
  branchPointIds?: string[];
  // Where the arms rejoin -- the earliest node EVERY live path out of the
  // branch reaches, computed at the flatten stage (before that, a branch
  // ending its method has no edge to where it continues). Absent when
  // they never rejoin, e.g. a guard whose only arm throws.
  convergesAt?: string;
}

export type LoopKind = "FOR" | "FOR_EACH" | "WHILE" | "DO" | "DO_WHILE";

export interface LoopGroup {
  id: string;
  kind: LoopKind;
  method?: string;
  line?: number;
  conditionCode?: string;
}

export interface FlowGraph {
  entryPoint?: string;
  nodes: FlowNode[];
  edges: FlowEdge[];
  rootId?: string;
  roots?: string[];
  orphans?: string[];
  branchGroups?: BranchGroup[];
  loopGroups?: LoopGroup[];
  // Semantic-analysis side-car keyed by an existing FlowNode.id.
  semanticFeatures?: Record<string, NodeSemanticFeatures>;
}

export type OperationRole =
  | "purposeful"
  | "atomic"
  | "expanded-container"
  | "exception-mechanic"
  | "structural";

export interface NodeSemanticFeatures {
  receiver?: string;
  receiverType?: string;
  arguments?: string[];
  argumentTypes?: string[];
  inputIdentifiers?: string[];
  fieldsRead?: string[];
  fieldsWritten?: string[];
  outputType?: string;
  dataSourceIds?: string[];
  dataConsumerIds?: string[];
  domainTypes?: string[];
  methodTerms?: string[];
  observedFeatures?: string[];
  role?: OperationRole;
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
  boundaryType?:
    | "branch-entry"
    | "branch-convergence"
    | "semantic-split"
    | "nested-region-retained"
    | "uncertain-fallback";
  decidedBy?: "systematic" | "llm" | "fallback";
  confidence?: number;
  evidence?: string[];
}

export interface Phase {
  nodes: string[];
  id?: string;
  label?: string;
  labelSourcePhaseId?: string;
  structuralAnchors?: string[];
  opened_by: Transition | null;
  transitions: Transition[];
}

export interface PhaseTree {
  entryPoint: string;
  phases: Phase[];
  complete?: boolean;
  unresolvedGates?: Array<{
    frontierId: string;
    candidateId: string;
    evidence: string[];
  }>;
}
