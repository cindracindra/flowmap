// Mirrors backend/src/flowmap/model/{node,edge,graph,phase,branch}.py field-for-field.

export type NodeType = "entry" | "call" | "structure" | "transfer" | "leaf" | "exit";

export type MethodExitKind = "return" | "throw" | "fallthrough";
export type TransferKind = "break" | "continue";

export type StructureRole = "entry" | "exit" | "decision";

export interface FlowNode {
  id: string;
  type: NodeType;
  calleeFullName?: string;
  callerMethod?: string;
  code?: string;
  structureGroupId?: string;
  structureRole?: StructureRole;
  targetStructureGroupId?: string;
  // Present only for visible type="transfer" nodes. Method
  // completion remains type="exit" plus exitKind.
  transferKind?: TransferKind;
  line?: number;
  sourceFile?: string;
  // Entry only: generated default constructor, not declared in source.
  implicitConstructor?: boolean;
  reason?: string;
  deadEnd?: boolean;
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
}

export type EdgeType = "sequence" | "invoke" | "data";

export interface FlowEdge {
  from: string;
  to: string;
  type: EdgeType;
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

export interface ConditionStage {
  id: string;
  nodeIds: string[];
  decisionNodeId?: string;
}

export interface ArmConditionStage {
  stageId: string;
  nodeIds: string[];
  outcome: boolean;
}

// Mirrors model/branch.py. One entry per IF/TRY control structure found
// during extraction. Loops are repetition regions in LoopGroup rather than
// mutually-exclusive branch arms; SWITCH is not split into arms yet.
export interface BranchArm {
  label: string;
  // No surviving operation in this arm. Path-level exits distinguish a
  // continuing empty arm from a terminal one.
  empty: boolean;
  // Path-level authoritative outcomes, including concrete destinations.
  exits: ArmExit[];
  // The `if`/`else if` condition selecting this arm -- per ARM, not per
  // group, because an else-if chain is one group with a different
  // condition per arm. Absent on `else` and on every TRY arm.
  conditionCode?: string;
  // Accumulated canonical condition outcomes required to select this final
  // arm in a flattened IF/else-if chain.
  conditionStages?: ArmConditionStage[];
  // TRY catch arms only: declared caught type, including a multi-catch
  // union when Joern exposes it as one type name.
  exceptionType?: string;
}

export interface ArmExit {
  kind: "return" | "throw" | "break" | "continue" | "continues";
  destinationNodeId?: string;
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
  entryNodeId: string;
  exitNodeId?: string;
  conditionStages?: ConditionStage[];
  arms: BranchArm[];
  // Outer arm selections required to reach this entire group. This remains
  // authoritative when filtering leaves the selected arm with no nodes.
  enclosingRequirements?: BranchRequirement[];
}

export type LoopKind = "FOR" | "FOR_EACH" | "WHILE" | "DO" | "DO_WHILE";

export interface LoopGroup {
  id: string;
  kind: LoopKind;
  method?: string;
  line?: number;
  conditionCode?: string;
  entryNodeId: string;
  exitNodeId: string;
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
  domainTypes?: string[];
  methodTerms?: string[];
  observedFeatures?: string[];
  role?: OperationRole;
}
