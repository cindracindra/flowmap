import { graphBundleRaw } from "virtual:flowmap-data";

import type {
  GraphBundle,
  MethodDefinition,
  OperationDefinition,
} from "../types/filteredGraph";

function record(value: unknown, field: string): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new Error(`[flowmap] graph bundle field ${field} must be an object`);
  }
  return value as Record<string, unknown>;
}

function stringArray(value: unknown, field: string): string[] {
  if (!Array.isArray(value) || value.some((item) => typeof item !== "string")) {
    throw new Error(`[flowmap] graph bundle field ${field} must be a string array`);
  }
  return value;
}

/** Validate the generated boundary before rendering relies on its references. */
export function loadGraphBundle(raw: unknown): GraphBundle {
  const bundle = record(raw, "root");
  const methods = record(bundle.methodsByEntryId, "methodsByEntryId");
  const operations = record(bundle.operationsById, "operationsById");
  const callers = record(bundle.callersByEntryId, "callersByEntryId");
  const operationMembership = record(
    bundle.operationIdsByMethodEntryId,
    "operationIdsByMethodEntryId",
  );
  const methodIds = new Set(Object.keys(methods));
  const operationIds = new Set(Object.keys(operations));

  for (const [entryId, rawMethod] of Object.entries(methods)) {
    const method = record(rawMethod, `methodsByEntryId.${entryId}`);
    if (method.entryId !== entryId) {
      throw new Error(`[flowmap] method key ${entryId} does not match its entryId`);
    }
    record(method.calls, `methodsByEntryId.${entryId}.calls`);
    stringArray(method.retainedCallNodeIds, `methodsByEntryId.${entryId}.retainedCallNodeIds`);
    if (!Array.isArray(method.nodes) || !Array.isArray(method.sequenceEdges)
      || !Array.isArray(method.exits) || !Array.isArray(method.phases)) {
      throw new Error(`[flowmap] method ${entryId} has malformed graph arrays`);
    }
  }

  for (const [operationId, rawOperation] of Object.entries(operations)) {
    const operation = record(rawOperation, `operationsById.${operationId}`);
    if (operation.id !== operationId || typeof operation.rootEntryId !== "string") {
      throw new Error(`[flowmap] operation key ${operationId} has inconsistent identity`);
    }
    if (!methodIds.has(operation.rootEntryId)) {
      throw new Error(`[flowmap] operation ${operationId} references a missing root method`);
    }
    for (const entryId of stringArray(
      operation.reachableMethodEntryIds,
      `operationsById.${operationId}.reachableMethodEntryIds`,
    )) {
      if (!methodIds.has(entryId)) {
        throw new Error(`[flowmap] operation ${operationId} reaches missing method ${entryId}`);
      }
    }
  }

  for (const entryId of methodIds) {
    for (const callerId of stringArray(callers[entryId] ?? [], `callersByEntryId.${entryId}`)) {
      if (!methodIds.has(callerId)) {
        throw new Error(`[flowmap] method ${entryId} has missing caller ${callerId}`);
      }
    }
    for (const operationId of stringArray(
      operationMembership[entryId] ?? [],
      `operationIdsByMethodEntryId.${entryId}`,
    )) {
      if (!operationIds.has(operationId)) {
        throw new Error(`[flowmap] method ${entryId} references missing operation ${operationId}`);
      }
    }
  }

  return raw as GraphBundle;
}

export interface FilteredGraphData {
  bundle: GraphBundle;
  methodsByEntryId: ReadonlyMap<string, MethodDefinition>;
  operationsById: ReadonlyMap<string, OperationDefinition>;
  callersByEntryId: ReadonlyMap<string, readonly string[]>;
  operationIdsByMethodEntryId: ReadonlyMap<string, readonly string[]>;
}

export function indexGraphBundle(bundle: GraphBundle): FilteredGraphData {
  return {
    bundle,
    methodsByEntryId: new Map(Object.entries(bundle.methodsByEntryId)),
    operationsById: new Map(Object.entries(bundle.operationsById)),
    callersByEntryId: new Map(Object.entries(bundle.callersByEntryId)),
    operationIdsByMethodEntryId: new Map(
      Object.entries(bundle.operationIdsByMethodEntryId),
    ),
  };
}

export const GRAPH_BUNDLE = loadGraphBundle(graphBundleRaw);
export const FILTERED_GRAPH_DATA = indexGraphBundle(GRAPH_BUNDLE);

export function methodDefinition(entryId: string): MethodDefinition | undefined {
  return FILTERED_GRAPH_DATA.methodsByEntryId.get(entryId);
}

export function operationDefinition(operationId: string): OperationDefinition | undefined {
  return FILTERED_GRAPH_DATA.operationsById.get(operationId);
}

export function callerEntryIds(entryId: string): readonly string[] {
  return FILTERED_GRAPH_DATA.callersByEntryId.get(entryId) ?? [];
}

export function operationIdsForMethod(entryId: string): readonly string[] {
  return FILTERED_GRAPH_DATA.operationIdsByMethodEntryId.get(entryId) ?? [];
}

