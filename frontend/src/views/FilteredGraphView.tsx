import { useCallback, useMemo, useState } from "react";
import { Flex, IconButton, Text } from "@radix-ui/themes";
import { ChevronLeft, ChevronRight } from "lucide-react";

import { GRAPH_BUNDLE } from "../data/filteredGraph";
import {
  callInstanceId,
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

export interface FilteredGraphViewProps {
  bundle?: GraphBundle;
  initialOperationId?: string;
  onOperationChange?: (operationId: string) => void;
}

export default function FilteredGraphView({ bundle = GRAPH_BUNDLE, initialOperationId, onOperationChange }: FilteredGraphViewProps) {
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
      ? projectVisibleGraph(bundle, operationId, expandedCalls, selectedBranchArms, selectedMethodEntryId ?? undefined, selectedTargetByCallInstanceId)
      : null,
    [bundle, operationId, expandedCalls, selectedBranchArms, selectedMethodEntryId, selectedTargetByCallInstanceId],
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
    const operation = bundle.operationIdsByMethodEntryId[entryId]?.[0];
    if (operation && operation !== operationId) {
      setOperationId(operation);
      onOperationChange?.(operation);
    }
    setExpandedCalls(new Set());
    setSelectedBranchArms(new Map());
    setSelectedTargetByCallInstanceId(new Map());
    setSelectedNodeId(null);
    setSelectedMethodEntryId(entryId);
  }, [bundle, operationId, onOperationChange]);
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
