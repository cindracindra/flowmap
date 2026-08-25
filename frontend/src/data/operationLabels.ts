import { topicOperationsRaw } from "virtual:flowmap-data";

import type { TopicOperation } from "../types/topics";

const operationsByTopic = topicOperationsRaw as Record<string, TopicOperation[]>;

/** Human-readable opseq labels, joined to graph_bundle operations by root ID. */
export const OPSEQ_LABELS_BY_ID = new Map<string, string>();

for (const operations of Object.values(operationsByTopic)) {
  for (const operation of operations) {
    if (operation.label) OPSEQ_LABELS_BY_ID.set(operation.id, operation.label);
  }
}

export function opseqLabel(operationId: string): string | undefined {
  return OPSEQ_LABELS_BY_ID.get(operationId);
}
