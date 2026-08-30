import assert from "node:assert/strict";

import { visibleNodeLoops } from "./src/lib/filteredGraphLoops.ts";
import type { VisibleNode } from "./src/lib/filteredGraphProjection.ts";
import type { GraphBundle } from "./src/types/filteredGraph.ts";

const instanceId = "operation:test/root:method";
const node = {
  id: `${instanceId}:work`, instanceId, definitionNodeId: "work", methodEntryId: "method",
  depth: 0, node: { id: "work", type: "call", loopIds: ["outer", "inner"] },
  retainedCall: false, expandable: false, expanded: false, recursiveCutoff: false,
  branchRequirements: [],
} satisfies VisibleNode;
const bundle = {
  methodsByEntryId: {
    method: {
      entryId: "entry", methodFullName: "Example.work", entry: { id: "entry", type: "entry" },
      nodes: [], sequenceEdges: [], calls: {}, exits: [], branchGroups: [], semanticFeatures: {}, phases: [],
      retainedCallNodeIds: [],
      loopGroups: [
        { id: "outer", kind: "FOR", line: 10, conditionCode: "i < n", entryNodeId: "outer-entry", exitNodeId: "outer-exit" },
        { id: "inner", kind: "WHILE", line: 11, conditionCode: "ready", entryNodeId: "inner-entry", exitNodeId: "inner-exit" },
      ],
    },
  },
  operationsById: {}, callersByEntryId: {}, operationIdsByMethodEntryId: {},
} satisfies GraphBundle;

const loops = visibleNodeLoops(node, bundle);
assert.deepEqual(loops.map((loop) => loop.label), ["for: i < n", "while: ready"]);
assert.equal(loops[0].instanceLoopId, `${instanceId}:loop:outer`);
assert.equal(loops[1].instanceLoopId, `${instanceId}:loop:inner`);
assert.equal(loops[0].entryNodeId, `${instanceId}:outer-entry`);
assert.equal(loops[0].exitNodeId, `${instanceId}:outer-exit`);

console.log("filtered graph loop checks passed");
