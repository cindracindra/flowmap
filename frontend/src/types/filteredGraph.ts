import type {
  BranchGroup,
  BranchRequirement,
  FlowEdge,
  FlowNode,
  LoopGroup,
  NodeSemanticFeatures,
} from "./flowmap";

export interface MethodPhaseDefinition {
  id: string;
  memberNodeIds: string[];
  label?: string;
}

export interface MethodExitDefinition {
  sourceNodeId: string;
  kind: "return" | "fallthrough" | "throw";
  branchRequirements?: BranchRequirement[];
}

export interface CallDefinition {
  callNodeId: string;
  targetEntryIds: string[];
  continuationIds: string[];
}

/** A reusable, definition-scoped method body. No IDs in here are instances. */
export interface MethodDefinition {
  entryId: string;
  methodFullName: string;
  entry: FlowNode;
  nodes: FlowNode[];
  sequenceEdges: FlowEdge[];
  calls: Record<string, CallDefinition>;
  exits: MethodExitDefinition[];
  branchGroups: BranchGroup[];
  loopGroups: LoopGroup[];
  semanticFeatures: Record<string, NodeSemanticFeatures>;
  phases: MethodPhaseDefinition[];
  retainedCallNodeIds: string[];
}

export interface OperationDefinition {
  id: string;
  rootEntryId: string;
  label?: string;
  reachableMethodEntryIds: string[];
}

/** Backend payload shared by every operation sequence in one analysis. */
export interface GraphBundle {
  methodsByEntryId: Record<string, MethodDefinition>;
  operationsById: Record<string, OperationDefinition>;
  // Direct upstream method dependencies, expressed as entry IDs. Call-site
  // detail remains in each method's calls index.
  callersByEntryId: Record<string, string[]>;
  operationIdsByMethodEntryId: Record<string, string[]>;
}
