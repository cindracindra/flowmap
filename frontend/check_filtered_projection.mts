import assert from "node:assert/strict";

import {
  branchInstanceId,
  callInstanceId,
  projectVisibleGraph,
} from "./src/lib/filteredGraphProjection.ts";
import type { GraphBundle, MethodDefinition } from "./src/types/filteredGraph.ts";

const method = (
  entryId: string,
  methodFullName: string,
  nodes: MethodDefinition["nodes"],
  sequenceEdges: MethodDefinition["sequenceEdges"],
  calls: MethodDefinition["calls"],
  phases: MethodDefinition["phases"],
  retainedCallNodeIds: string[] = [],
): MethodDefinition => ({
  entryId,
  methodFullName,
  entry: { id: entryId, type: "entry", calleeFullName: methodFullName },
  nodes,
  sequenceEdges,
  calls,
  exits: nodes.filter((node) => node.type === "exit").map((node) => ({
    sourceNodeId: node.id,
    kind: node.exitKind!,
  })),
  branchGroups: [],
  loopGroups: [],
  semanticFeatures: {},
  phases,
  retainedCallNodeIds,
});

const root = method(
  "root", "Example.root:void()",
  [
    { id: "call", type: "call", callerMethod: "Example.root:void()" },
    { id: "after", type: "call", callerMethod: "Example.root:void()" },
    { id: "root-end", type: "exit", callerMethod: "Example.root:void()", exitKind: "fallthrough" },
  ],
  [
    { from: "root", to: "call", type: "sequence" },
    { from: "call", to: "after", type: "sequence" },
    { from: "after", to: "root-end", type: "sequence" },
  ],
  { call: { callNodeId: "call", targetEntryIds: ["callee"], continuationIds: ["after"] } },
  [{ id: "root-phase", memberNodeIds: ["call", "after"] }],
);
const callee = method(
  "callee", "Example.callee:void()",
  [
    { id: "work", type: "call", callerMethod: "Example.callee:void()" },
    { id: "later", type: "call", callerMethod: "Example.callee:void()" },
    { id: "return", type: "exit", callerMethod: "Example.callee:void()", exitKind: "return" },
    { id: "throw", type: "exit", callerMethod: "Example.callee:void()", exitKind: "throw", deadEnd: true },
  ],
  [
    { from: "callee", to: "work", type: "sequence" },
    { from: "work", to: "later", type: "sequence" },
    { from: "later", to: "return", type: "sequence" },
    { from: "callee", to: "throw", type: "sequence" },
  ],
  {},
  [{ id: "callee-phase", memberNodeIds: ["work"] }],
);
const bundle: GraphBundle = {
  methodsByEntryId: { root, callee },
  operationsById: { op: { id: "op", rootEntryId: "root", reachableMethodEntryIds: ["root", "callee"] } },
  callersByEntryId: { root: [], callee: ["root"] },
  operationIdsByMethodEntryId: { root: ["op"], callee: ["op"] },
};

const collapsed = projectVisibleGraph(bundle, "op", new Set())!;
const rootInstance = "operation:op/root:root";
const expandedId = callInstanceId(rootInstance, "call");
const expanded = projectVisibleGraph(bundle, "op", new Set([expandedId]))!;
const callerSequence = (projection: typeof collapsed) => projection.edges
  .filter((edge) => edge.kind === "sequence" && edge.from.startsWith(`${rootInstance}:`))
  .map((edge) => `${edge.from}->${edge.to}`)
  .sort();

assert.deepEqual(callerSequence(expanded), callerSequence(collapsed), "expansion must preserve caller sequence edges");
assert.equal(collapsed.edges.some((edge) => edge.kind === "invoke"), false);
assert.equal(expanded.edges.filter((edge) => edge.kind === "invoke").length, 1);
assert.equal(expanded.edges.some((edge) => !["sequence", "invoke"].includes(edge.kind)), false);
assert.equal(expanded.nodes.some((node) => node.node.exitKind === "return"), true);
assert.equal(expanded.nodes.some((node) => node.node.exitKind === "throw" && node.node.deadEnd), true);

const callerCall = expanded.nodes.find((node) => node.definitionNodeId === "call")!;
const calleeWork = expanded.nodes.find((node) => node.definitionNodeId === "work")!;
assert.equal(calleeWork.phase?.id, callerCall.phase?.id, "non-retained single-phase callees must inherit the caller phase");

root.retainedCallNodeIds = ["call"];
const retained = projectVisibleGraph(bundle, "op", new Set([expandedId]))!;
const retainedWork = retained.nodes.find((node) => node.definitionNodeId === "work")!;
assert.notEqual(retainedWork.phase?.id, callerCall.phase?.id, "retained callee must use its own phase");
assert.equal(retainedWork.phase?.definitionPhaseId, "callee-phase");

const wrapper = method(
  "wrapper", "Example.wrapper:void()",
  [
    { id: "wrapper-work", type: "call", callerMethod: "Example.wrapper:void()" },
    { id: "inner-call", type: "call", callerMethod: "Example.wrapper:void()" },
    { id: "wrapper-end", type: "exit", callerMethod: "Example.wrapper:void()", exitKind: "return" },
  ],
  [
    { from: "wrapper", to: "wrapper-work", type: "sequence" },
    { from: "wrapper-work", to: "inner-call", type: "sequence" },
    { from: "inner-call", to: "wrapper-end", type: "sequence" },
  ],
  { "inner-call": { callNodeId: "inner-call", targetEntryIds: ["callee"], continuationIds: ["wrapper-end"] } },
  [{ id: "wrapper-phase", memberNodeIds: ["wrapper-work"] }],
  ["inner-call"],
);
bundle.methodsByEntryId.wrapper = wrapper;
root.calls.call.targetEntryIds = ["wrapper"];
const transitivelyRetained = projectVisibleGraph(bundle, "op", new Set())!;
assert.equal(
  transitivelyRetained.nodes.find((node) => node.definitionNodeId === "call")?.retainedCalleePhaseCount,
  2,
  "collapsed retained-call badges include unique local and nested retained phases",
);
root.calls.call.targetEntryIds = ["callee"];

const branchDefinition = {
  id: "cs1",
  kind: "IF",
  arms: [
    {
      label: "if",
      empty: false,
      exits: [{
        kind: "continues" as const,
        destinationNodeId: "return",
        branchRequirements: [{ groupId: "cs1", armLabel: "if" }],
      }],
    },
    { label: "else", empty: false, exits: [{ kind: "throw" as const, destinationNodeId: "throw" }] },
  ],
};
callee.branchGroups = [branchDefinition];
callee.nodes[0].branchArms = [{ groupId: "cs1", armLabel: "if" }];
callee.nodes[3].branchArms = [{ groupId: "cs1", armLabel: "else" }];
callee.sequenceEdges[0].branchRequirements = [{ groupId: "cs1", armLabel: "if" }];
callee.sequenceEdges[1].branchRequirements = [{ groupId: "cs1", armLabel: "if" }];
callee.sequenceEdges[3].branchRequirements = [{ groupId: "cs1", armLabel: "else" }];
callee.exits[0].branchRequirements = [{ groupId: "cs1", armLabel: "if" }];
callee.exits[1].branchRequirements = [{ groupId: "cs1", armLabel: "else" }];

const branched = projectVisibleGraph(bundle, "op", new Set([expandedId]))!;
const calleeInstance = `${expandedId}/target:0:callee`;
const visibleBranchId = branchInstanceId(calleeInstance, "cs1");
const visibleBranch = branched.branchGroups.find((group) => group.id === visibleBranchId)!;
assert.equal(visibleBranch.definitionBranchId, "cs1");
assert.deepEqual(visibleBranch.entryPredecessorIds, []);
assert.equal(visibleBranch.arms[0].exits[0].destinationNodeId, `${calleeInstance}:return`);
assert.equal(
  branched.nodes.find((node) => node.definitionNodeId === "work")?.branchRequirements[0].groupId,
  visibleBranchId,
);
assert.equal(
  branched.edges.find((edge) => edge.to === `${calleeInstance}:work`)?.branchRequirements?.[0].groupId,
  visibleBranchId,
);
assert.equal(
  branched.exits.find((exit) => exit.sourceNodeId === `${calleeInstance}:return`)
    ?.branchRequirements?.[0].groupId,
  visibleBranchId,
);
assert.equal(callee.branchGroups[0].id, "cs1", "projection must not mutate definition branch IDs");
assert.equal(callee.branchGroups[0].id, "cs1");

assert.equal(visibleBranch.selectedArmLabel, "if", "if is the deterministic preferred arm");
assert.equal(
  branched.nodes.some((node) => node.definitionNodeId === "work"),
  true,
  "the default arm's node must be visible",
);
assert.equal(
  branched.edges.some((edge) => edge.to === `${calleeInstance}:work`),
  true,
  "the default arm's edge must be visible",
);
assert.equal(
  branched.exits.some((exit) => exit.sourceNodeId === `${calleeInstance}:return`),
  true,
  "the default arm's exit must be visible",
);

const elseSelected = projectVisibleGraph(
  bundle,
  "op",
  new Set([expandedId]),
  new Map([[visibleBranchId, "else"]]),
)!;
assert.equal(
  elseSelected.branchGroups.find((group) => group.id === visibleBranchId)?.selectedArmLabel,
  "else",
);
assert.equal(
  elseSelected.nodes.some((node) => node.definitionNodeId === "work"),
  false,
  "nodes outside the selected arm must be filtered",
);
assert.equal(
  elseSelected.nodes.some((node) => node.definitionNodeId === "later"),
  false,
  "a selected throw path must make later method-local sequence unreachable",
);
assert.equal(
  elseSelected.nodes.some((node) => node.definitionNodeId === "after"),
  true,
  "a callee throw must not delete the caller-local continuation",
);
assert.equal(
  elseSelected.edges.some((edge) => edge.to === `${calleeInstance}:work`),
  false,
  "branch-restricted edges and edges with hidden endpoints must be filtered",
);
assert.equal(
  elseSelected.exits.some((exit) => exit.sourceNodeId === `${calleeInstance}:return`),
  false,
  "exits outside the selected arm must be filtered",
);
assert.equal(
  elseSelected.edges.some((edge) =>
    !elseSelected.nodes.some((node) => node.id === edge.from)
    || !elseSelected.nodes.some((node) => node.id === edge.to)),
  false,
  "every visible edge must have two visible endpoints",
);

const invalidSelection = projectVisibleGraph(
  bundle,
  "op",
  new Set([expandedId]),
  new Map([[visibleBranchId, "missing-arm"]]),
)!;
assert.equal(
  invalidSelection.branchGroups.find((group) => group.id === visibleBranchId)?.selectedArmLabel,
  "if",
  "invalid persisted selections must fall back deterministically",
);

const nested = method(
  "nested-entry",
  "Example.nested:void()",
  [
    { id: "outer-if", type: "call", branchArms: [{ groupId: "outer", armLabel: "if" }] },
    { id: "inner-if", type: "call", branchArms: [
      { groupId: "outer", armLabel: "if" },
      { groupId: "inner", armLabel: "if" },
    ] },
    { id: "inner-else", type: "call", branchArms: [
      { groupId: "outer", armLabel: "if" },
      { groupId: "inner", armLabel: "else" },
    ] },
    { id: "outer-else", type: "call", branchArms: [{ groupId: "outer", armLabel: "else" }] },
  ],
  [
    { from: "nested-entry", to: "outer-if", type: "sequence", branchRequirements: [{ groupId: "outer", armLabel: "if" }] },
    { from: "outer-if", to: "inner-if", type: "sequence", branchRequirements: [
      { groupId: "outer", armLabel: "if" },
      { groupId: "inner", armLabel: "if" },
    ] },
    { from: "outer-if", to: "inner-else", type: "sequence", branchRequirements: [
      { groupId: "outer", armLabel: "if" },
      { groupId: "inner", armLabel: "else" },
    ] },
    { from: "nested-entry", to: "outer-else", type: "sequence", branchRequirements: [{ groupId: "outer", armLabel: "else" }] },
  ],
  {},
  [],
);
nested.branchGroups = [
  {
    id: "outer",
    kind: "IF",
    arms: [
      { label: "if", empty: false },
      { label: "else", empty: false },
    ],
  },
  {
    id: "inner",
    kind: "IF",
    arms: [
      { label: "if", empty: false },
      { label: "else", empty: false },
    ],
  },
];
const nestedBundle: GraphBundle = {
  methodsByEntryId: { "nested-entry": nested },
  operationsById: {
    nested: { id: "nested", rootEntryId: "nested-entry", reachableMethodEntryIds: ["nested-entry"] },
  },
  callersByEntryId: { "nested-entry": [] },
  operationIdsByMethodEntryId: { "nested-entry": ["nested"] },
};
const nestedInstance = "operation:nested/root:nested-entry";
const outerBranchId = branchInstanceId(nestedInstance, "outer");
const innerBranchId = branchInstanceId(nestedInstance, "inner");
const nestedDefault = projectVisibleGraph(nestedBundle, "nested", new Set())!;
assert.deepEqual(
  nestedDefault.nodes.map((node) => node.definitionNodeId).sort(),
  ["inner-if", "nested-entry", "outer-if"],
  "nested nodes must satisfy both default selections",
);
const nestedElse = projectVisibleGraph(
  nestedBundle,
  "nested",
  new Set(),
  new Map([[innerBranchId, "else"]]),
)!;
assert.equal(nestedElse.nodes.some((node) => node.definitionNodeId === "inner-if"), false);
assert.equal(nestedElse.nodes.some((node) => node.definitionNodeId === "inner-else"), true);
const outerElse = projectVisibleGraph(
  nestedBundle,
  "nested",
  new Set(),
  new Map([[outerBranchId, "else"]]),
)!;
assert.equal(outerElse.nodes.some((node) => node.definitionNodeId === "outer-else"), true);
assert.equal(outerElse.nodes.some((node) => node.definitionNodeId.startsWith("inner-")), false);

const emptyArmMethod = method(
  "empty-entry",
  "Example.emptyArm:void()",
  [
    {
      id: "branch-point",
      type: "call",
      branchArms: [{ groupId: "empty-branch", armLabel: "if" }],
    },
    {
      id: "if-work",
      type: "call",
      branchArms: [{ groupId: "empty-branch", armLabel: "if" }],
    },
    { id: "after-empty-arm", type: "call" },
  ],
  [
    { from: "empty-entry", to: "branch-point", type: "sequence" },
    {
      from: "branch-point",
      to: "if-work",
      type: "sequence",
      branchRequirements: [{ groupId: "empty-branch", armLabel: "if" }],
    },
    {
      from: "branch-point",
      to: "after-empty-arm",
      type: "sequence",
      branchRequirements: [{ groupId: "empty-branch", armLabel: "else" }],
    },
  ],
  {},
  [],
);
emptyArmMethod.branchGroups = [{
  id: "empty-branch",
  kind: "IF",
  arms: [
    { label: "if", empty: false, exits: [{ kind: "continues" }] },
    {
      label: "else",
      empty: true,
      exits: [{ kind: "continues", destinationNodeId: "after-empty-arm" }],
    },
  ],
}];
const emptyArmBundle: GraphBundle = {
  methodsByEntryId: { "empty-entry": emptyArmMethod },
  operationsById: {
    empty: { id: "empty", rootEntryId: "empty-entry", reachableMethodEntryIds: ["empty-entry"] },
  },
  callersByEntryId: { "empty-entry": [] },
  operationIdsByMethodEntryId: { "empty-entry": ["empty"] },
};
const emptyInstance = "operation:empty/root:empty-entry";
const emptySelection = projectVisibleGraph(
  emptyArmBundle,
  "empty",
  new Set(),
  new Map([[branchInstanceId(emptyInstance, "empty-branch"), "else"]]),
)!;
assert.equal(emptySelection.nodes.some((node) => node.definitionNodeId === "if-work"), false);
assert.equal(
  emptySelection.nodes.some((node) => node.definitionNodeId === "after-empty-arm"),
  true,
  "an empty continuing arm must retain its reachable subsequent sequence",
);

const polyRoot = method(
  "poly-root", "Example.poly:void()",
  [
    { id: "poly-call", type: "call", callerMethod: "Example.poly:void()" },
    { id: "poly-end", type: "exit", callerMethod: "Example.poly:void()", exitKind: "fallthrough" },
  ],
  [
    { from: "poly-root", to: "poly-call", type: "sequence" },
    { from: "poly-call", to: "poly-end", type: "sequence" },
  ],
  { "poly-call": { callNodeId: "poly-call", targetEntryIds: ["impl-a", "impl-b"], continuationIds: ["poly-end"] } },
  [],
);
const implA = method("impl-a", "A.run:void()", [], [], {}, []);
const implB = method("impl-b", "B.run:void()", [], [], {}, []);
const polyBundle: GraphBundle = {
  methodsByEntryId: { "poly-root": polyRoot, "impl-a": implA, "impl-b": implB },
  operationsById: { poly: { id: "poly", rootEntryId: "poly-root", reachableMethodEntryIds: ["poly-root", "impl-a", "impl-b"] } },
  callersByEntryId: { "poly-root": [], "impl-a": ["poly-root"], "impl-b": ["poly-root"] },
  operationIdsByMethodEntryId: { "poly-root": ["poly"], "impl-a": ["poly"], "impl-b": ["poly"] },
};
const polyInstance = "operation:poly/root:poly-root";
const polyCallInstance = callInstanceId(polyInstance, "poly-call");
const defaultDispatch = projectVisibleGraph(polyBundle, "poly", new Set([polyCallInstance]))!;
assert.deepEqual(
  defaultDispatch.edges.filter((edge) => edge.kind === "invoke").map((edge) => edge.to),
  [`${polyCallInstance}/target:0:impl-a:impl-a`],
  "polymorphic expansion must initially show only its default implementation",
);
assert.deepEqual(defaultDispatch.branchGroups.find((group) => group.id === polyCallInstance)?.arms.map((arm) => arm.label), ["impl-a", "impl-b"]);
const alternateDispatch = projectVisibleGraph(
  polyBundle, "poly", new Set([polyCallInstance]), new Map(), undefined,
  new Map([[polyCallInstance, "impl-b"]]),
)!;
assert.deepEqual(
  alternateDispatch.edges.filter((edge) => edge.kind === "invoke").map((edge) => edge.to),
  [`${polyCallInstance}/target:1:impl-b:impl-b`],
  "dispatch selection must be scoped to the expanded call instance",
);

const bridgedMethod = method(
  "bridged-entry", "Example.bridged:void()",
  [
    { id: "decision", type: "structure", structureGroupId: "guard", structureRole: "decision" },
    { id: "work", type: "call", callerMethod: "Example.bridged:void()", branchArms: [{ groupId: "guard", armLabel: "if" }] },
  ],
  [
    { from: "bridged-entry", to: "decision", type: "sequence" },
    { from: "decision", to: "work", type: "sequence", branchRequirements: [{ groupId: "guard", armLabel: "if" }] },
  ],
  {},
  [{ id: "work-phase", memberNodeIds: ["work"] }],
);
bridgedMethod.branchGroups = [{
  id: "guard",
  kind: "IF",
  entryNodeId: "decision",
  arms: [
    { label: "if", empty: false, exits: [{ kind: "continues" }] },
    { label: "else", empty: true, exits: [{ kind: "continues" }] },
  ],
}];
const bridgedBundle: GraphBundle = {
  methodsByEntryId: { "bridged-entry": bridgedMethod },
  operationsById: { bridged: { id: "bridged", rootEntryId: "bridged-entry", reachableMethodEntryIds: ["bridged-entry"] } },
  callersByEntryId: { "bridged-entry": [] },
  operationIdsByMethodEntryId: { "bridged-entry": ["bridged"] },
};
const bridgedProjection = projectVisibleGraph(bridgedBundle, "bridged", new Set())!;
const bridgedInstance = "operation:bridged/root:bridged-entry";
assert.equal(bridgedProjection.nodes.some((node) => node.node.type === "structure"), false);
assert(bridgedProjection.edges.some((edge) =>
  edge.from === `${bridgedInstance}:bridged-entry`
  && edge.to === `${bridgedInstance}:work`
  && edge.branchRequirements?.[0]?.groupId === branchInstanceId(bridgedInstance, "guard")));
assert.deepEqual(
  bridgedProjection.branchGroups[0].entryPredecessorIds,
  [`${bridgedInstance}:bridged-entry`],
);

// Authoritative entry/exit anchors survive projection as boundary metadata,
// while canonical condition calls become arm-row aliases rather than graph
// rows. This covers the Package 19 contract end to end.
const anchored = method(
  "anchored-entry", "Example.anchored:void()",
  [
    { id: "g-entry", type: "structure", structureGroupId: "g", structureRole: "entry" },
    { id: "condition", type: "call", code: "ready()" },
    { id: "decision", type: "structure", structureGroupId: "g", structureRole: "decision" },
    { id: "body", type: "call", branchArms: [{ groupId: "g", armLabel: "if" }] },
    { id: "g-exit", type: "structure", structureGroupId: "g", structureRole: "exit" },
    { id: "after", type: "call" },
  ],
  [
    { from: "anchored-entry", to: "g-entry", type: "sequence" },
    { from: "g-entry", to: "condition", type: "sequence" },
    { from: "condition", to: "decision", type: "sequence" },
    { from: "decision", to: "body", type: "sequence", branchRequirements: [{ groupId: "g", armLabel: "if" }] },
    { from: "body", to: "g-exit", type: "sequence", branchRequirements: [{ groupId: "g", armLabel: "if" }] },
    { from: "g-exit", to: "after", type: "sequence" },
  ],
  {}, [],
);
anchored.branchGroups = [{
  id: "g", kind: "IF", entryNodeId: "g-entry", exitNodeId: "g-exit",
  conditionStages: [{ id: "stage-1", nodeIds: ["condition"], decisionNodeId: "decision" }],
  arms: [
    { label: "if", empty: false, conditionStages: [{ stageId: "stage-1", nodeIds: ["condition"], outcome: true }], exits: [{ kind: "continues", destinationNodeId: "g-exit" }] },
    { label: "else", empty: true, conditionStages: [{ stageId: "stage-1", nodeIds: ["condition"], outcome: false }], exits: [{ kind: "continues", destinationNodeId: "g-exit" }] },
  ],
}];
const anchoredBundle: GraphBundle = {
  methodsByEntryId: { "anchored-entry": anchored },
  operationsById: { anchored: { id: "anchored", rootEntryId: "anchored-entry", reachableMethodEntryIds: ["anchored-entry"] } },
  callersByEntryId: { "anchored-entry": [] }, operationIdsByMethodEntryId: { "anchored-entry": ["anchored"] },
};
const anchoredProjection = projectVisibleGraph(anchoredBundle, "anchored", new Set())!;
const anchoredInstance = "operation:anchored/root:anchored-entry";
const anchoredGroup = anchoredProjection.branchGroups[0];
assert.equal(anchoredProjection.nodes.some((node) => node.definitionNodeId === "condition"), false);
assert.deepEqual(anchoredGroup.entryPredecessorIds, [`${anchoredInstance}:anchored-entry`]);
assert.deepEqual(anchoredGroup.entrySuccessorIds, [`${anchoredInstance}:body`]);
assert.deepEqual(anchoredGroup.exitPredecessorIds, [`${anchoredInstance}:body`]);
assert.deepEqual(anchoredGroup.continuationIds, [`${anchoredInstance}:after`]);
assert.equal(anchoredGroup.conditionStages?.[0].code, "ready()");

console.log("filtered projection checks passed");
