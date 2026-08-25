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

const branchDefinition = {
  id: "cs1",
  kind: "IF",
  branchPointIds: ["work"],
  convergesAt: "return",
  arms: [
    {
      label: "if",
      firstCallId: "work",
      empty: false,
      terminus: "continues" as const,
      targetIds: ["return"],
      exits: [{
        kind: "continues" as const,
        frontierIds: ["work"],
        targetIds: ["return"],
        branchRequirements: [{ groupId: "cs1", armLabel: "if" }],
      }],
    },
    { label: "else", firstCallId: "throw", empty: false, terminus: "throw" as const },
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
assert.deepEqual(visibleBranch.branchPointIds, [`${calleeInstance}:work`]);
assert.equal(visibleBranch.arms[0].firstCallId, `${calleeInstance}:work`);
assert.deepEqual(visibleBranch.arms[0].targetIds, [`${calleeInstance}:return`]);
assert.deepEqual(visibleBranch.arms[0].exits?.[0].frontierIds, [`${calleeInstance}:work`]);
assert.equal(visibleBranch.arms[0].exits?.[0].branchRequirements?.[0].groupId, visibleBranchId);
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
assert.deepEqual(callee.branchGroups[0].branchPointIds, ["work"]);

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
      { label: "if", firstCallId: "outer-if", empty: false },
      { label: "else", firstCallId: "outer-else", empty: false },
    ],
  },
  {
    id: "inner",
    kind: "IF",
    arms: [
      { label: "if", firstCallId: "inner-if", empty: false },
      { label: "else", firstCallId: "inner-else", empty: false },
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
  branchPointIds: ["branch-point"],
  arms: [
    { label: "if", firstCallId: "if-work", empty: false, terminus: "continues" },
    {
      label: "else",
      empty: true,
      terminus: "continues",
      targetIds: ["after-empty-arm"],
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

console.log("filtered projection checks passed");
