import { useCallback, useMemo, useState } from "react";
import { Flex, IconButton, Text } from "@radix-ui/themes";
import { ChevronLeft, ChevronRight } from "lucide-react";

import { GRAPH_BUNDLE } from "../data/filteredGraph";
import {
  branchInstanceId,
  callInstanceId,
  instanceNodeId,
  projectVisibleGraph,
  type BranchInstanceId,
  type CallInstanceId,
  type VisibleNode,
} from "../lib/filteredGraphProjection";
import type { GraphBundle } from "../types/filteredGraph";
import FilteredGraphCanvas from "./filtered/FilteredGraphCanvas";
import FilteredGraphDetails from "./filtered/FilteredGraphDetails";
import FilteredGraphLeftPanel from "./filtered/FilteredGraphLeftPanel";
import { MONO } from "../lib/ui";

interface MethodPathStep {
  callerEntryId: string;
  callNodeId: string;
  targetEntryId: string;
  targetIndex: number;
}

function methodCallPath(bundle: GraphBundle, rootEntryId: string, targetEntryId: string): MethodPathStep[] | null {
  const queue: Array<{ entryId: string; steps: MethodPathStep[] }> = [{ entryId: rootEntryId, steps: [] }];
  const visited = new Set<string>();
  while (queue.length > 0) {
    const current = queue.shift()!;
    if (current.entryId === targetEntryId) return current.steps;
    if (visited.has(current.entryId)) continue;
    visited.add(current.entryId);
    const method = bundle.methodsByEntryId[current.entryId];
    if (!method) continue;
    for (const call of Object.values(method.calls)) {
      for (const [targetIndex, target] of call.targetEntryIds.entries()) {
        if (visited.has(target)) continue;
        queue.push({
          entryId: target,
          steps: [...current.steps, {
            callerEntryId: current.entryId,
            callNodeId: call.callNodeId,
            targetEntryId: target,
            targetIndex,
          }],
        });
      }
    }
  }
  return null;
}

export interface FilteredGraphViewProps {
  bundle?: GraphBundle;
  initialOperationId?: string;
  onOperationChange?: (operationId: string) => void;
  leftPanelVariant?: "default" | "operation-methods";
}

export default function FilteredGraphView({ bundle = GRAPH_BUNDLE, initialOperationId, onOperationChange, leftPanelVariant = "default" }: FilteredGraphViewProps) {
  const firstOperationId = initialOperationId ?? Object.keys(bundle.operationsById)[0] ?? null;
  const [operationId, setOperationId] = useState<string | null>(firstOperationId);
  const [expandedCalls, setExpandedCalls] = useState<Set<string>>(() => new Set());
  const [selectedBranchArms, setSelectedBranchArms] = useState<Map<BranchInstanceId, string>>(
    () => new Map(),
  );
  const [selectedTargetByCallInstanceId, setSelectedTargetByCallInstanceId] = useState<Map<CallInstanceId, string>>(
    () => new Map(),
  );
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [selectedMethodEntryId, setSelectedMethodEntryId] = useState<string | null>(null);
  const [leftPanelOpen, setLeftPanelOpen] = useState(true);
  const [detailsPanelOpen, setDetailsPanelOpen] = useState(true);
  const projection = useMemo(
    () => operationId
      ? projectVisibleGraph(
          bundle,
          operationId,
          expandedCalls,
          selectedBranchArms,
          leftPanelVariant === "operation-methods" ? undefined : selectedMethodEntryId ?? undefined,
          selectedTargetByCallInstanceId,
        )
      : null,
    [bundle, operationId, expandedCalls, selectedBranchArms, selectedMethodEntryId, selectedTargetByCallInstanceId, leftPanelVariant],
  );
  const selectedNode = projection?.nodes.find((node) => node.id === selectedNodeId) ?? null;
  const currentMethodEntryId = selectedMethodEntryId
    ?? (operationId ? bundle.operationsById[operationId]?.rootEntryId : null)
    ?? null;

  const selectOperation = useCallback((id: string) => {
    setOperationId(id); setExpandedCalls(new Set()); setSelectedBranchArms(new Map()); setSelectedTargetByCallInstanceId(new Map()); setSelectedNodeId(null); setSelectedMethodEntryId(null);
    onOperationChange?.(id);
  }, [onOperationChange]);
  const selectMethod = useCallback((entryId: string) => {
    if (leftPanelVariant === "operation-methods" && operationId) {
      const operation = bundle.operationsById[operationId];
      if (!operation) return;
      const path = methodCallPath(bundle, operation.rootEntryId, entryId);
      if (!path) return;
      const nextExpandedCalls = new Set<string>();
      const nextSelectedTargets = new Map<CallInstanceId, string>();
      const nextSelectedBranches = new Map<BranchInstanceId, string>();
      let instanceId = `operation:${operationId}/root:${operation.rootEntryId}`;
      for (const step of path) {
        const method = bundle.methodsByEntryId[step.callerEntryId];
        const callNode = method
          ? [method.entry, ...method.nodes].find((node) => node.id === step.callNodeId)
          : undefined;
        for (const requirement of callNode?.branchArms ?? []) {
          nextSelectedBranches.set(
            branchInstanceId(instanceId, requirement.groupId),
            requirement.armLabel,
          );
        }
        const callId = callInstanceId(instanceId, step.callNodeId);
        nextExpandedCalls.add(callId);
        nextSelectedTargets.set(callId, step.targetEntryId);
        instanceId = `${callId}/target:${step.targetIndex}:${step.targetEntryId}`;
      }
      setExpandedCalls(nextExpandedCalls);
      setSelectedBranchArms(nextSelectedBranches);
      setSelectedTargetByCallInstanceId(nextSelectedTargets);
      setSelectedMethodEntryId(entryId);
      setSelectedNodeId(instanceNodeId(instanceId, entryId));
      return;
    }
    const currentOperation = operationId ? bundle.operationsById[operationId] : undefined;
    const belongsToCurrentOperation = currentOperation
      ? currentOperation.rootEntryId === entryId || currentOperation.reachableMethodEntryIds.includes(entryId)
      : false;
    const operation = belongsToCurrentOperation
      ? operationId
      : bundle.operationIdsByMethodEntryId[entryId]?.[0];
    if (operation && operation !== operationId) {
      setOperationId(operation);
      onOperationChange?.(operation);
    }
    setExpandedCalls(new Set());
    setSelectedBranchArms(new Map());
    setSelectedTargetByCallInstanceId(new Map());
    setSelectedNodeId(null);
    setSelectedMethodEntryId(entryId);
  }, [bundle, operationId, onOperationChange, leftPanelVariant]);
  const toggleCall = useCallback((node: VisibleNode) => {
    const id = callInstanceId(node.instanceId, node.definitionNodeId);
    setExpandedCalls((previous) => {
      const next = new Set(previous);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  }, []);
  const selectBranchArm = useCallback((branchId: BranchInstanceId, armLabel: string, kind: string) => {
    if (kind === "DISPATCH") {
      setSelectedTargetByCallInstanceId((previous) => {
        const next = new Map(previous);
        next.set(branchId, armLabel);
        return next;
      });
      setSelectedNodeId(null);
      return;
    }
    setSelectedBranchArms((previous) => {
      const next = new Map(previous);
      next.set(branchId, armLabel);
      return next;
    });
    setSelectedNodeId(null);
  }, []);
  const selectNode = useCallback((node: VisibleNode) => setSelectedNodeId(node.id), []);

  return (
    <Flex height="100%" style={{ minHeight: 0, position: "relative", fontFamily: MONO }}>
      {leftPanelOpen && (
        <FilteredGraphLeftPanel
          bundle={bundle}
          selectedOperationId={operationId}
          selectedMethodEntryId={currentMethodEntryId}
          onSelectOperation={selectOperation}
          onSelectMethod={selectMethod}
          onCollapse={() => setLeftPanelOpen(false)}
          variant={leftPanelVariant}
        />
      )}
      {!leftPanelOpen && (
        <Flex
          direction="column"
          align="center"
          width="34px"
          flexShrink="0"
          pt="2"
          aria-label="Collapsed left panel"
          style={{
            borderRight: "1px solid var(--gray-a5)",
            background: "var(--color-panel-solid)",
          }}
        >
          <IconButton
            size="1"
            variant="ghost"
            color="gray"
            aria-label="Show left panel"
            onClick={() => setLeftPanelOpen(true)}
          >
            <ChevronRight size={14} />
          </IconButton>
        </Flex>
      )}
      {projection ? <FilteredGraphCanvas projection={projection} selectedNodeId={selectedNodeId} onSelectNode={selectNode} onToggleCall={toggleCall} onSelectBranchArm={selectBranchArm} bundle={bundle} currentMethodEntryId={currentMethodEntryId} onSelectMethod={selectMethod} onSelectOperation={selectOperation} /> : (
        <Flex flexGrow="1" align="center" justify="center"><Text size="2" color="gray">Generate graph_bundle.json to choose an operation.</Text></Flex>
      )}
      {detailsPanelOpen ? (
        <FilteredGraphDetails node={selectedNode} bundle={bundle} onCollapse={() => setDetailsPanelOpen(false)} />
      ) : (
        <Flex direction="column" align="center" width="34px" flexShrink="0" pt="2"
          aria-label="Collapsed node details"
          style={{ borderLeft: "1px solid var(--gray-a5)", background: "var(--color-panel-solid)" }}>
          <IconButton size="1" variant="ghost" color="gray" aria-label="Show node details"
            onClick={() => setDetailsPanelOpen(true)}>
            <ChevronLeft size={14} />
          </IconButton>
        </Flex>
      )}
    </Flex>
  );
}
