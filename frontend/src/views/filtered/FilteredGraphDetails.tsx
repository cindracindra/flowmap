import { Badge, Box, Flex, IconButton, ScrollArea, Text } from "@radix-ui/themes";
import { ChevronRight } from "lucide-react";

import type { VisibleNode } from "../../lib/filteredGraphProjection";
import { visibleNodeLoops } from "../../lib/filteredGraphLoops";
import { MONO } from "../../lib/ui";
import type { GraphBundle } from "../../types/filteredGraph";

function compactType(type: string): string {
  return type.trim().replace(/(?:[\w$]+\.)+([\w$]+)/g, "$1");
}

/** `package.Class.method:return(package.Type,int)` -> `Class.method(Type, int)`. */
function compactMethodSignature(fullName: string): string {
  const separator = fullName.indexOf(":");
  const qualified = separator >= 0 ? fullName.slice(0, separator) : fullName;
  const signature = separator >= 0 ? fullName.slice(separator + 1) : "";
  const parts = qualified.split(".");
  const methodName = parts.pop() ?? qualified;
  const className = parts.pop();
  const open = signature.indexOf("(");
  const close = signature.lastIndexOf(")");
  const argumentsText = open >= 0 && close > open ? signature.slice(open + 1, close) : "";
  const argumentsList = argumentsText
    ? argumentsText.split(",").map(compactType).join(", ")
    : "";
  return `${className ? `${className}.` : ""}${methodName}(${argumentsList})`;
}

export interface FilteredGraphDetailsProps {
  node: VisibleNode | null;
  bundle: GraphBundle;
  onCollapse: () => void;
}

export default function FilteredGraphDetails({ node, bundle, onCollapse }: FilteredGraphDetailsProps) {
  const method = node ? bundle.methodsByEntryId[node.methodEntryId] : undefined;
  const loops = node ? visibleNodeLoops(node, bundle) : [];
  const operationLabel = node?.node.exitKind === "fallthrough"
    ? "return"
    : node?.node.code ?? node?.node.calleeFullName ?? node?.definitionNodeId;
  const exitLabel = node?.node.exitKind === "fallthrough"
    ? "implicit return"
    : node?.node.exitKind === "return"
      ? "explicit return"
      : "dead end";

  return (
    <Flex direction="column" width="300px" flexShrink="0" style={{ borderLeft: "1px solid var(--gray-a5)", background: "var(--color-panel-solid)" }}>
      <Flex align="center" justify="between" px="3" height="38px" flexShrink="0" style={{ borderBottom: "1px solid var(--gray-a5)" }}>
        <Text size="1" weight="bold">Node details</Text>
        <Flex align="center" gap="2">
          {node && <Badge>{node.node.type}</Badge>}
          <IconButton size="1" variant="ghost" color="gray" aria-label="Hide node details" onClick={onCollapse}>
            <ChevronRight size={14} />
          </IconButton>
        </Flex>
      </Flex>
      {!node ? (
        <Flex flexGrow="1" align="center" justify="center" p="4">
          <Text size="1" color="gray" align="center">Select a node to inspect its definition.</Text>
        </Flex>
      ) : (
        <ScrollArea style={{ flex: 1 }}><Flex direction="column" gap="3" p="3">
          <Box><Text size="1" color="gray" as="div">Operations</Text><Text size="2" style={{ fontFamily: MONO }}>{operationLabel}</Text></Box>
          <Box><Text size="1" color="gray" as="div">Owning method</Text><Text size="1" style={{ fontFamily: MONO }}>{method ? compactMethodSignature(method.methodFullName) : node.methodEntryId}</Text></Box>
          <Box><Text size="1" color="gray" as="div">Source</Text><Text size="1" style={{ fontFamily: MONO }}>{node.node.sourceFile ?? "unknown"}{node.node.line ? `:${node.node.line}` : ""}</Text></Box>
          {node.node.exitKind && <Box><Text size="1" color="gray" as="div">Exit</Text><Badge color="gray">{exitLabel}</Badge></Box>}
          {node.phase && <Box><Text size="1" color="gray" as="div">Method-local phase</Text><Text size="1">{node.phase.label ?? `Phase ${node.phase.index + 1}`}</Text></Box>}
          {loops.length > 0 && <Box>
            <Text size="1" color="gray" as="div" mb="1">Loops</Text>
            <Flex direction="column" gap="2">
              {loops.map((loop) => <Box key={loop.instanceLoopId}>
                <Flex align="center" gap="1" wrap="wrap">
                  <Badge color="orange">{loop.kind?.toLowerCase().replaceAll("_", " ") ?? "loop"}</Badge>
                  {loop.conditionCode && <Text size="1" style={{ fontFamily: MONO }}>{loop.conditionCode}</Text>}
                </Flex>
                {loop.line && <Text size="1" color="gray" as="div">Source line {loop.line}</Text>}
              </Box>)}
            </Flex>
          </Box>}
          {node.recursiveCutoff && <Flex gap="1" wrap="wrap">
            <Badge color="orange">recursive cutoff</Badge>
          </Flex>}
        </Flex></ScrollArea>
      )}
    </Flex>
  );
}
