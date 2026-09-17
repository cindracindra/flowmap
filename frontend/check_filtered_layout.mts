import assert from "node:assert/strict";

import { layoutFilteredGraph } from "./src/lib/filteredGraphLayout.ts";
import type {
  VisibleGraphProjection,
  VisibleNode,
} from "./src/lib/filteredGraphProjection.ts";

const rootInstance = "operation:test/root:root";
const childOne = `${rootInstance}/call:call-1/target:0:child-one`;
const childTwo = `${rootInstance}/call:call-2/target:0:child-two`;
const node = (
  id: string,
  instanceId: string,
  depth: number,
  definitionNodeId: string,
  options: Partial<VisibleNode> = {},
): VisibleNode => ({
  id,
  instanceId,
  methodEntryId: instanceId === rootInstance ? "root" : definitionNodeId.split("-")[0],
  definitionNodeId,
  depth,
  node: { id: definitionNodeId, type: definitionNodeId.includes("entry") ? "entry" : "call" },
  retainedCall: false,
  expandable: false,
  expanded: false,
  recursiveCutoff: false,
  branchRequirements: [],
  ...options,
});

const projection: VisibleGraphProjection = {
  rootId: `${rootInstance}:entry`,
  nodes: [
    node(`${rootInstance}:entry`, rootInstance, 0, "entry"),
    node(`${rootInstance}:call-1`, rootInstance, 0, "call-1", {
      expanded: true,
      expandable: true,
      branchRequirements: [{ groupId: `${rootInstance}/branch:g1`, armLabel: "if" }],
    }),
    node(`${childOne}:entry-one`, childOne, 1, "entry-one"),
    node(`${childOne}:work-one`, childOne, 1, "work-one", {
      node: { id: "work-one", type: "call", code: "a very long expanded operation label" },
    }),
    node(`${rootInstance}:call-2`, rootInstance, 0, "call-2", {
      expanded: true,
      expandable: true,
    }),
    node(`${childTwo}:entry-two`, childTwo, 1, "entry-two"),
    node(`${childTwo}:work-two`, childTwo, 1, "work-two"),
    node(`${rootInstance}:after`, rootInstance, 0, "after"),
  ],
  edges: [
    { from: `${rootInstance}:entry`, to: `${rootInstance}:call-1`, type: "sequence", kind: "sequence", branchRequirements: [{ groupId: `${rootInstance}/branch:g1`, armLabel: "if" }] },
    { from: `${rootInstance}:call-1`, to: `${childOne}:entry-one`, type: "invoke", kind: "invoke" },
    { from: `${rootInstance}:call-1`, to: `${rootInstance}:call-2`, type: "sequence", kind: "sequence" },
    { from: `${rootInstance}:call-2`, to: `${childTwo}:entry-two`, type: "invoke", kind: "invoke" },
    { from: `${rootInstance}:call-2`, to: `${rootInstance}:after`, type: "sequence", kind: "sequence" },
  ],
  branchGroups: [{
    id: `${rootInstance}/branch:g1`,
    instanceId: rootInstance,
    definitionBranchId: "g1",
    kind: "IF",
    line: 1,
    selectedArmLabel: "if",
    decisionReachable: true,
    entryPredecessorIds: [`${rootInstance}:entry`],
    decisionPredecessorIds: [],
    entrySuccessorIds: [`${rootInstance}:call-1`],
    exitPredecessorIds: [`${rootInstance}:call-1`],
    continuationIds: [`${rootInstance}:call-2`],
    arms: [
      { label: "if", empty: false, exits: [{ kind: "continues" }] },
      { label: "else", empty: true, exits: [{ kind: "continues" }] },
    ],
  }],
  exits: [],
};

const layout = layoutFilteredGraph(projection);
const y = (id: string) => layout.positions.get(id)!.y;
assert.equal(y(`${rootInstance}:call-2`) - y(`${rootInstance}:call-1`), 78,
  "an expanded callee must not consume rows in its caller");
assert.equal(y(`${rootInstance}:after`) - y(`${rootInstance}:call-2`), 78,
  "the caller continuation remains locally compact");
assert.equal(y(`${childOne}:entry-one`) - y(`${rootInstance}:call-1`), 46,
  "an invoke edge should descend slightly from the call to the callee entry");
assert(y(`${childTwo}:entry-two`) > y(`${childOne}:work-one`),
  "overlapping sibling expansion subtrees are packed apart");

const panel = layout.branches[0];
assert(panel.ownedNodeIds.has(`${rootInstance}:call-1`));
assert(!panel.ownedNodeIds.has(`${childOne}:work-one`), "expanded descendants stay outside caller branch panels");
assert(!panel.ownedNodeIds.has(`${rootInstance}:call-2`), "later caller node is not branch-owned");
assert(y(`${rootInstance}:call-2`) > panel.y + panel.height, "later caller node must render below the branch panel");

const terminalInstance = "operation:test/root:terminal";
const terminalProjection: VisibleGraphProjection = {
  rootId: `${terminalInstance}:entry`,
  nodes: [
    node(`${terminalInstance}:entry`, terminalInstance, 0, "entry"),
    node(`${terminalInstance}:return`, terminalInstance, 0, "return", {
      node: { id: "return", type: "exit", exitKind: "return", code: "return 1;" },
      branchRequirements: [{ groupId: `${terminalInstance}/branch:g2`, armLabel: "if" }],
    }),
  ],
  edges: [{
    from: `${terminalInstance}:entry`, to: `${terminalInstance}:return`,
    type: "sequence", kind: "sequence",
    branchRequirements: [{ groupId: `${terminalInstance}/branch:g2`, armLabel: "if" }],
  }],
  branchGroups: [{
    id: `${terminalInstance}/branch:g2`, instanceId: terminalInstance,
    definitionBranchId: "g2", kind: "IF", selectedArmLabel: "if",
    decisionReachable: true,
    entryPredecessorIds: [`${terminalInstance}:entry`], entrySuccessorIds: [`${terminalInstance}:return`],
    decisionPredecessorIds: [],
    exitPredecessorIds: [], continuationIds: [],
    arms: [
      { label: "if", empty: true, exits: [{ kind: "return" }] },
      { label: "else", empty: true, exits: [{ kind: "continues" }] },
    ],
  }],
  exits: [{
    instanceId: terminalInstance,
    sourceNodeId: `${terminalInstance}:return`, kind: "return",
    branchRequirements: [{ groupId: `${terminalInstance}/branch:g2`, armLabel: "if" }],
  }],
};

const terminalLayout = layoutFilteredGraph(terminalProjection);
const terminalPanel = terminalLayout.branches[0];
assert.equal(terminalPanel.compactEmpty, false, "return-only arm is content-backed, not visually empty");
assert(terminalPanel.ownedNodeIds.has(`${terminalInstance}:return`));
const returnY = terminalLayout.positions.get(`${terminalInstance}:return`)!.y;
assert(returnY >= terminalPanel.y && returnY <= terminalPanel.y + terminalPanel.height);

const emptyInstance = "operation:test/root:empty";
const emptyProjection: VisibleGraphProjection = {
  rootId: `${emptyInstance}:entry`,
  nodes: [
    node(`${emptyInstance}:entry`, emptyInstance, 0, "entry"),
    node(`${emptyInstance}:after`, emptyInstance, 0, "after"),
  ],
  edges: [{
    from: `${emptyInstance}:entry`, to: `${emptyInstance}:after`,
    type: "sequence", kind: "sequence",
    branchRequirements: [{ groupId: `${emptyInstance}/branch:g3`, armLabel: "else" }],
  }],
  branchGroups: [{
    id: `${emptyInstance}/branch:g3`, instanceId: emptyInstance,
    definitionBranchId: "g3", kind: "IF", selectedArmLabel: "else",
    decisionReachable: true,
    // A bridged decision can report the condition/fork as its entry successor.
    // Empty-arm placement must still use the post-branch continuation.
    entryPredecessorIds: [`${emptyInstance}:entry`], entrySuccessorIds: [`${emptyInstance}:entry`],
    decisionPredecessorIds: [],
    exitPredecessorIds: [], continuationIds: [`${emptyInstance}:after`],
    arms: [
      { label: "if", empty: false, exits: [{ kind: "return" }] },
      { label: "else", empty: true, exits: [{ kind: "continues" }] },
    ],
  }],
  exits: [],
};

const emptyLayout = layoutFilteredGraph(emptyProjection);
const emptyPanel = emptyLayout.branches[0];
const continuationY = emptyLayout.positions.get(`${emptyInstance}:after`)!.y;
assert.equal(emptyPanel.compactEmpty, true);
assert(emptyLayout.height < 500, "an empty arm anchored at its fork must not trigger repeated shifts");
assert(
  emptyPanel.y + emptyPanel.height + 22 <= continuationY,
  "empty panel must end before its continuation node",
);

// A method definition can serialize the shared continuation before catch
// members. The selected sequence path, rather than that array order, must
// determine vertical placement.
const catchInstance = "operation:test/root:catch";
const catchProjection: VisibleGraphProjection = {
  rootId: `${catchInstance}:entry`,
  nodes: [
    node(`${catchInstance}:entry`, catchInstance, 0, "entry"),
    node(`${catchInstance}:branch`, catchInstance, 0, "branch"),
    node(`${catchInstance}:publish`, catchInstance, 0, "publish"),
    node(`${catchInstance}:return`, catchInstance, 0, "return", {
      node: { id: "return", type: "exit", exitKind: "fallthrough" },
    }),
    node(`${catchInstance}:catch-head`, catchInstance, 0, "catch-head"),
    node(`${catchInstance}:catch-body`, catchInstance, 0, "catch-body"),
  ],
  edges: [
    { from: `${catchInstance}:entry`, to: `${catchInstance}:branch`, type: "sequence", kind: "sequence" },
    { from: `${catchInstance}:branch`, to: `${catchInstance}:catch-head`, type: "sequence", kind: "sequence" },
    { from: `${catchInstance}:catch-head`, to: `${catchInstance}:catch-body`, type: "sequence", kind: "sequence" },
    { from: `${catchInstance}:catch-body`, to: `${catchInstance}:publish`, type: "sequence", kind: "sequence" },
    { from: `${catchInstance}:publish`, to: `${catchInstance}:return`, type: "sequence", kind: "sequence" },
  ],
  branchGroups: [],
  exits: [],
};

const catchLayout = layoutFilteredGraph(catchProjection);
const catchY = (suffix: string) => catchLayout.positions.get(`${catchInstance}:${suffix}`)!.y;
assert(catchY("branch") < catchY("catch-head"));
assert(catchY("catch-head") < catchY("catch-body"));
assert(catchY("catch-body") < catchY("publish"), "catch body must precede its shared continuation");
assert(catchY("publish") < catchY("return"), "method exit must follow the shared continuation");

// A hidden inline conditional can leave a short bypass and a longer visible
// call path that converge at one operation. The join must wait for the longer
// path instead of being emitted immediately by a preorder traversal.
const ternaryInstance = "operation:test/root:hidden-ternary";
const ternaryProjection: VisibleGraphProjection = {
  rootId: `${ternaryInstance}:fork`,
  nodes: [
    node(`${ternaryInstance}:fork`, ternaryInstance, 0, "fork"),
    node(`${ternaryInstance}:construct`, ternaryInstance, 0, "construct"),
    node(`${ternaryInstance}:throw`, ternaryInstance, 0, "throw", {
      node: { id: "throw", type: "exit", exitKind: "throw" },
    }),
    node(`${ternaryInstance}:get-class`, ternaryInstance, 0, "get-class"),
    node(`${ternaryInstance}:get-name`, ternaryInstance, 0, "get-name"),
  ],
  edges: [
    // The backend may encounter the ternary's short/null route first.
    { from: `${ternaryInstance}:fork`, to: `${ternaryInstance}:construct`, type: "sequence", kind: "sequence" },
    { from: `${ternaryInstance}:fork`, to: `${ternaryInstance}:get-class`, type: "sequence", kind: "sequence" },
    { from: `${ternaryInstance}:get-class`, to: `${ternaryInstance}:get-name`, type: "sequence", kind: "sequence" },
    { from: `${ternaryInstance}:get-name`, to: `${ternaryInstance}:construct`, type: "sequence", kind: "sequence" },
    { from: `${ternaryInstance}:construct`, to: `${ternaryInstance}:throw`, type: "sequence", kind: "sequence" },
  ],
  branchGroups: [],
  exits: [],
};
const ternaryLayout = layoutFilteredGraph(ternaryProjection);
const ternaryY = (suffix: string) => ternaryLayout.positions.get(`${ternaryInstance}:${suffix}`)!.y;
assert(ternaryY("get-class") < ternaryY("get-name"));
assert(ternaryY("get-name") < ternaryY("construct"), "a convergence must follow every visible incoming path");
assert(ternaryY("construct") < ternaryY("throw"));

const expandedForkInstance = "operation:test/root:expanded-fork";
const expandedForkChild = `${expandedForkInstance}/call:branch-call/target:0:child`;
const expandedForkProjection: VisibleGraphProjection = {
  rootId: `${expandedForkInstance}:branch-call`,
  nodes: [
    node(`${expandedForkInstance}:branch-call`, expandedForkInstance, 0, "branch-call", {
      expanded: true,
      expandable: true,
    }),
    node(`${expandedForkChild}:entry`, expandedForkChild, 1, "entry"),
    node(`${expandedForkChild}:return`, expandedForkChild, 1, "return", {
      node: { id: "return", type: "exit", exitKind: "fallthrough" },
    }),
    node(`${expandedForkInstance}:after`, expandedForkInstance, 0, "after"),
  ],
  edges: [
    { from: `${expandedForkInstance}:branch-call`, to: `${expandedForkChild}:entry`, type: "invoke", kind: "invoke" },
    { from: `${expandedForkChild}:entry`, to: `${expandedForkChild}:return`, type: "sequence", kind: "sequence" },
    {
      from: `${expandedForkInstance}:branch-call`, to: `${expandedForkInstance}:after`,
      type: "sequence", kind: "sequence",
      branchRequirements: [{ groupId: `${expandedForkInstance}/branch:g4`, armLabel: "else" }],
    },
  ],
  branchGroups: [{
    id: `${expandedForkInstance}/branch:g4`, instanceId: expandedForkInstance,
    definitionBranchId: "g4", kind: "TRY", selectedArmLabel: "else",
    decisionReachable: true,
    entryPredecessorIds: [`${expandedForkInstance}:branch-call`],
    decisionPredecessorIds: [],
    entrySuccessorIds: [`${expandedForkInstance}:after`], exitPredecessorIds: [],
    continuationIds: [`${expandedForkInstance}:after`],
    arms: [{ label: "else", empty: true, exits: [{ kind: "continues" }] }],
  }],
  exits: [],
};

const expandedForkLayout = layoutFilteredGraph(expandedForkProjection);
const expandedForkPanel = expandedForkLayout.branches[0];
const expandedForkY = expandedForkLayout.positions.get(`${expandedForkInstance}:branch-call`)!.y;
const expandedAfterY = expandedForkLayout.positions.get(`${expandedForkInstance}:after`)!.y;
assert(
  expandedForkPanel.y > expandedForkY && expandedForkPanel.y + expandedForkPanel.height < expandedAfterY,
  "branch controls stay in the caller flow instead of waiting for the expanded callee",
);

// TRY starts lexically before an IF in its protected body, but its outcome
// split occurs after that IF. Panel order must follow fork topology, not the
// source line where each control structure begins.
const nestedTryInstance = "operation:test/root:nested-try";
const innerGroupId = `${nestedTryInstance}/branch:inner-if`;
const tryGroupId = `${nestedTryInstance}/branch:outer-try`;
const nestedTryProjection: VisibleGraphProjection = {
  rootId: `${nestedTryInstance}:entry`,
  nodes: [
    node(`${nestedTryInstance}:entry`, nestedTryInstance, 0, "entry"),
    node(`${nestedTryInstance}:if-fork`, nestedTryInstance, 0, "if-fork"),
    node(`${nestedTryInstance}:if-body`, nestedTryInstance, 0, "if-body", {
      branchRequirements: [{ groupId: innerGroupId, armLabel: "if" }],
    }),
    node(`${nestedTryInstance}:try-fork`, nestedTryInstance, 0, "try-fork"),
    node(`${nestedTryInstance}:after`, nestedTryInstance, 0, "after"),
  ],
  edges: [
    { from: `${nestedTryInstance}:entry`, to: `${nestedTryInstance}:if-fork`, type: "sequence", kind: "sequence" },
    {
      from: `${nestedTryInstance}:if-fork`, to: `${nestedTryInstance}:if-body`,
      type: "sequence", kind: "sequence",
      branchRequirements: [{ groupId: innerGroupId, armLabel: "if" }],
    },
    { from: `${nestedTryInstance}:if-body`, to: `${nestedTryInstance}:try-fork`, type: "sequence", kind: "sequence" },
    {
      from: `${nestedTryInstance}:try-fork`, to: `${nestedTryInstance}:after`,
      type: "sequence", kind: "sequence",
      branchRequirements: [{ groupId: tryGroupId, armLabel: "noCatch" }],
    },
  ],
  branchGroups: [
    {
      id: tryGroupId, instanceId: nestedTryInstance, definitionBranchId: "outer-try",
      kind: "TRY", line: 10, selectedArmLabel: "noCatch",
      decisionReachable: true,
      entryPredecessorIds: [`${nestedTryInstance}:try-fork`], entrySuccessorIds: [`${nestedTryInstance}:if-fork`],
      decisionPredecessorIds: [],
      exitPredecessorIds: [], continuationIds: [`${nestedTryInstance}:after`],
      arms: [{ label: "noCatch", empty: true, exits: [{ kind: "continues" }] }],
    },
    {
      id: innerGroupId, instanceId: nestedTryInstance, definitionBranchId: "inner-if",
      kind: "IF", line: 12, selectedArmLabel: "if",
      decisionReachable: true,
      entryPredecessorIds: [`${nestedTryInstance}:if-fork`], entrySuccessorIds: [`${nestedTryInstance}:if-body`],
      decisionPredecessorIds: [],
      exitPredecessorIds: [`${nestedTryInstance}:if-body`], continuationIds: [`${nestedTryInstance}:after`],
      arms: [{ label: "if", empty: false, exits: [{ kind: "continues" }] }],
    },
  ],
  exits: [],
};

const nestedTryLayout = layoutFilteredGraph(nestedTryProjection);
const nestedPanels = new Map(nestedTryLayout.branches.map((branch) => [branch.group.id, branch]));
assert(
  nestedPanels.get(innerGroupId)!.y < nestedPanels.get(tryGroupId)!.y,
  "nested IF panel must appear before the later TRY outcome split despite TRY's earlier source line",
);
for (const branch of nestedTryLayout.branches) {
  for (const nodeId of branch.ownedNodeIds) {
    const point = nestedTryLayout.positions.get(nodeId)!;
    assert(
      point.y >= branch.y && point.y <= branch.y + branch.height,
      `panel ${branch.group.id} must be rebuilt when row reservation moves owned node ${nodeId}`,
    );
  }
}

// A selected short-circuit arm can leave both its condition continuation and
// body continuation reachable from the same node. CFG reachability stays
// authoritative, while source location decides which DFS successor is laid
// out first.
const conditionInstance = "operation:test/root:add-item";
const conditionGroup = `${conditionInstance}/branch:condition`;
const conditionProjection: VisibleGraphProjection = {
  rootId: `${conditionInstance}:entry`,
  nodes: [
    node(`${conditionInstance}:entry`, conditionInstance, 0, "entry", {
      node: { id: "entry", type: "entry", line: 94 },
    }),
    node(`${conditionInstance}:get-cart`, conditionInstance, 0, "get-cart", {
      node: { id: "get-cart", type: "call", code: "getCart(session)", line: 101 },
      branchRequirements: [{ groupId: conditionGroup, armLabel: "if" }],
    }),
    node(`${conditionInstance}:return`, conditionInstance, 0, "return", {
      node: { id: "return", type: "exit", exitKind: "return", line: 110 },
    }),
    node(`${conditionInstance}:trim`, conditionInstance, 0, "trim", {
      node: { id: "trim", type: "call", code: "workingItemId.trim()", line: 97 },
    }),
    node(`${conditionInstance}:is-empty`, conditionInstance, 0, "is-empty", {
      node: { id: "is-empty", type: "call", code: "workingItemId.trim().isEmpty()", line: 97 },
    }),
  ],
  edges: [
    { from: `${conditionInstance}:entry`, to: `${conditionInstance}:get-cart`, type: "sequence", kind: "sequence",
      branchRequirements: [{ groupId: conditionGroup, armLabel: "if" }] },
    { from: `${conditionInstance}:entry`, to: `${conditionInstance}:trim`, type: "sequence", kind: "sequence" },
    { from: `${conditionInstance}:get-cart`, to: `${conditionInstance}:return`, type: "sequence", kind: "sequence" },
    { from: `${conditionInstance}:trim`, to: `${conditionInstance}:is-empty`, type: "sequence", kind: "sequence" },
    { from: `${conditionInstance}:is-empty`, to: `${conditionInstance}:get-cart`, type: "sequence", kind: "sequence",
      branchRequirements: [{ groupId: conditionGroup, armLabel: "if" }] },
    { from: `${conditionInstance}:is-empty`, to: `${conditionInstance}:trim`, type: "sequence", kind: "sequence" },
  ],
  branchGroups: [{
    id: conditionGroup,
    instanceId: conditionInstance,
    definitionBranchId: "condition",
    kind: "IF",
    selectedArmLabel: "if",
    decisionReachable: true,
    entryPredecessorIds: [`${conditionInstance}:entry`], entrySuccessorIds: [`${conditionInstance}:trim`],
    decisionPredecessorIds: [`${conditionInstance}:entry`, `${conditionInstance}:is-empty`],
    exitPredecessorIds: [`${conditionInstance}:get-cart`], continuationIds: [],
    arms: [
      { label: "if", empty: false, exits: [{ kind: "continues" }] },
      { label: "else", empty: true, exits: [{ kind: "return" }] },
    ],
  }],
  exits: [],
};

const conditionLayout = layoutFilteredGraph(conditionProjection);
const conditionY = (id: string) => conditionLayout.positions.get(`${conditionInstance}:${id}`)!.y;
assert(conditionY("trim") < conditionY("is-empty"), "nested condition calls retain CFG order");
assert(conditionY("is-empty") < conditionY("get-cart"), "earlier condition route wins a DFS successor tie");
assert(conditionY("get-cart") < conditionY("return"), "selected body remains before its return");
assert(
  conditionLayout.branches[0].y > conditionY("is-empty")
    && conditionLayout.branches[0].y < conditionY("get-cart"),
  "branch controls follow the latest condition predecessor and precede the selected body",
);

// Projection has already bridged structural decisions while retaining their
// explicit entry boundary as panel metadata.
const decisionsInstance = "operation:test/root:decisions";
const outerDecisionGroup = `${decisionsInstance}/branch:outer`;
const innerDecisionGroup = `${decisionsInstance}/branch:inner`;
const decisionsProjection: VisibleGraphProjection = {
  rootId: `${decisionsInstance}:entry`,
  nodes: [
    node(`${decisionsInstance}:entry`, decisionsInstance, 0, "entry"),
    node(`${decisionsInstance}:compare`, decisionsInstance, 0, "compare", {
      node: { id: "compare", type: "call", code: "compareTo(value)", line: 11 },
      branchRequirements: [{ groupId: outerDecisionGroup, armLabel: "if" }],
    }),
    node(`${decisionsInstance}:return`, decisionsInstance, 0, "return", {
      node: { id: "return", type: "exit", exitKind: "return", code: "return value", line: 13 },
      branchRequirements: [
        { groupId: outerDecisionGroup, armLabel: "if" },
        { groupId: innerDecisionGroup, armLabel: "if" },
      ],
    }),
  ],
  edges: [
    { from: `${decisionsInstance}:entry`, to: `${decisionsInstance}:compare`, type: "sequence", kind: "sequence",
      branchRequirements: [{ groupId: outerDecisionGroup, armLabel: "if" }] },
    { from: `${decisionsInstance}:compare`, to: `${decisionsInstance}:return`, type: "sequence", kind: "sequence",
      branchRequirements: [
        { groupId: outerDecisionGroup, armLabel: "if" },
        { groupId: innerDecisionGroup, armLabel: "if" },
      ] },
  ],
  branchGroups: [
    {
      id: outerDecisionGroup, instanceId: decisionsInstance, definitionBranchId: "outer",
      kind: "IF", selectedArmLabel: "if", entryPredecessorIds: [`${decisionsInstance}:entry`],
      decisionReachable: true,
      decisionPredecessorIds: [],
      entrySuccessorIds: [`${decisionsInstance}:compare`], exitPredecessorIds: [`${decisionsInstance}:compare`], continuationIds: [],
      arms: [
        { label: "if", empty: false, exits: [{ kind: "continues" }] },
        { label: "else", empty: true, exits: [{ kind: "continues" }] },
      ],
    },
    {
      id: innerDecisionGroup, instanceId: decisionsInstance, definitionBranchId: "inner",
      kind: "IF", selectedArmLabel: "if", entryPredecessorIds: [`${decisionsInstance}:compare`],
      decisionReachable: true,
      decisionPredecessorIds: [],
      entrySuccessorIds: [], exitPredecessorIds: [], continuationIds: [],
      enclosingRequirements: [{ groupId: outerDecisionGroup, armLabel: "if" }],
      arms: [
        { label: "if", empty: true, exits: [{ kind: "return" }] },
        { label: "else", empty: true, exits: [{ kind: "continues" }] },
      ],
    },
  ],
  exits: [{
    instanceId: decisionsInstance, sourceNodeId: `${decisionsInstance}:return`, kind: "return",
    branchRequirements: [
      { groupId: outerDecisionGroup, armLabel: "if" },
      { groupId: innerDecisionGroup, armLabel: "if" },
    ],
  }],
};
const decisionsLayout = layoutFilteredGraph(decisionsProjection);
const decisionsY = (id: string) => decisionsLayout.positions.get(`${decisionsInstance}:${id}`)!.y;
assert(decisionsY("entry") < decisionsY("compare"));
assert(decisionsY("compare") < decisionsY("return"));
assert(decisionsLayout.height < 1000, "consecutive branch panels must not create runaway empty height");
const nestedDecisionPanels = new Map(
  decisionsLayout.branches.map((branch) => [branch.group.id, branch]),
);
const outerDecisionPanel = nestedDecisionPanels.get(outerDecisionGroup)!;
const innerDecisionPanel = nestedDecisionPanels.get(innerDecisionGroup)!;
assert(innerDecisionPanel, "reachable empty nested branch must retain a compact panel");
assert(outerDecisionPanel.x <= innerDecisionPanel.x);
assert(outerDecisionPanel.y <= innerDecisionPanel.y);
assert(
  innerDecisionPanel.y >= outerDecisionPanel.y + 46 + 10,
  "nested panel controls must be placed below the parent controls",
);
assert(
  outerDecisionPanel.x + outerDecisionPanel.width
    >= innerDecisionPanel.x + innerDecisionPanel.width,
  "outer panel must contain the empty inner panel horizontally",
);
assert(
  outerDecisionPanel.y + outerDecisionPanel.height
    >= innerDecisionPanel.y + innerDecisionPanel.height,
  "outer panel must contain the empty inner panel vertically",
);

const elseIfInstance = "operation:test/root:else-if";
const elseIfGroup = `${elseIfInstance}/branch:chain`;
const elseIfProjection: VisibleGraphProjection = {
  rootId: `${elseIfInstance}:entry`,
  nodes: [
    node(`${elseIfInstance}:entry`, elseIfInstance, 0, "entry"),
    node(`${elseIfInstance}:body`, elseIfInstance, 0, "body", {
      branchRequirements: [{ groupId: elseIfGroup, armLabel: "elseif2" }],
    }),
    node(`${elseIfInstance}:after`, elseIfInstance, 0, "after"),
  ],
  edges: [
    { from: `${elseIfInstance}:entry`, to: `${elseIfInstance}:body`, type: "sequence", kind: "sequence",
      branchRequirements: [{ groupId: elseIfGroup, armLabel: "elseif2" }] },
    { from: `${elseIfInstance}:body`, to: `${elseIfInstance}:after`, type: "sequence", kind: "sequence" },
  ],
  branchGroups: [{
    id: elseIfGroup, instanceId: elseIfInstance, definitionBranchId: "chain",
    kind: "IF", selectedArmLabel: "elseif2",
    decisionReachable: true,
    entryPredecessorIds: [`${elseIfInstance}:entry`],
    decisionPredecessorIds: [],
    entrySuccessorIds: [`${elseIfInstance}:body`],
    exitPredecessorIds: [`${elseIfInstance}:body`],
    continuationIds: [`${elseIfInstance}:after`],
    conditionStages: [
      { id: "s1", nodeIds: [], code: "first()" },
      { id: "s2", nodeIds: [], code: "second()" },
      { id: "s3", nodeIds: [], code: "third()" },
    ],
    arms: [
      { label: "if", empty: false, conditionStages: [{ stageId: "s1", nodeIds: [], outcome: true }] },
      { label: "elseif1", empty: false, conditionStages: [
        { stageId: "s1", nodeIds: [], outcome: false },
        { stageId: "s2", nodeIds: [], outcome: true },
      ] },
      { label: "elseif2", empty: false, conditionStages: [
        { stageId: "s1", nodeIds: [], outcome: false },
        { stageId: "s2", nodeIds: [], outcome: false },
        { stageId: "s3", nodeIds: [], outcome: true },
      ] },
      { label: "else", empty: true, conditionStages: [
        { stageId: "s1", nodeIds: [], outcome: false },
        { stageId: "s2", nodeIds: [], outcome: false },
        { stageId: "s3", nodeIds: [], outcome: false },
      ], exits: [{ kind: "continues" }] },
    ],
  }],
  exits: [],
};
const elseIfLayout = layoutFilteredGraph(elseIfProjection);
const elseIfPanel = elseIfLayout.branches[0];
assert(
  elseIfLayout.positions.get(`${elseIfInstance}:after`)!.y
    >= elseIfPanel.y + elseIfPanel.height + 12,
  "else-if continuation must remain below its complete panel",
);

console.log("filtered graph layout checks passed");
