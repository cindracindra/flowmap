import { Box, Card, Flex, Separator, Text } from "@radix-ui/themes";
import { Repeat2 } from "lucide-react";

import { EDGE_STYLES, NODE_STYLES, NODE_TYPES, type EdgeClass } from "../lib/nodeStyles";
import type { NodeType } from "../types/flowmap";

const EDGE_LEGEND_LABEL: Record<EdgeClass, string> = {
  sequence: "seq",
  invoke: "invoke",
  return: "return",
  fallback: "fallback",
};

function LegendNodeShape({ type }: { type: NodeType }) {
  const colors = NODE_STYLES[type];
  return (
    <svg width="22" height="22" viewBox="0 0 22 22" style={{ flexShrink: 0 }} aria-hidden="true">
      {type === "entry" ? (
        <rect
          x="5"
          y="5"
          width="12"
          height="12"
          transform="rotate(45 11 11)"
          fill={colors.fill}
          stroke={colors.stroke}
        />
      ) : (
        <circle
          cx="11"
          cy="11"
          r={type === "call" ? 7 : 6}
          fill={colors.fill}
          stroke={colors.stroke}
          strokeDasharray={type === "leaf" ? "3 2" : undefined}
        />
      )}
    </svg>
  );
}

export default function GraphLegend() {
  return (
    <Card size="2" style={{ width: 330, maxHeight: "calc(100vh - 150px)", overflowY: "auto" }}>
      <Text size="1" weight="bold" color="gray" style={{ textTransform: "uppercase", letterSpacing: "0.08em" }}>
        Node types
      </Text>
      <Flex direction="column" gap="2" mt="2">
        {NODE_TYPES.map((type) => (
          <Flex key={type} align="center" gap="2">
            <LegendNodeShape type={type} />
            <Box>
              <Text size="1" weight="bold" as="div">{NODE_STYLES[type].label}</Text>
              <Text size="1" color="gray" as="div">{NODE_STYLES[type].explanation}</Text>
            </Box>
          </Flex>
        ))}
      </Flex>

      <Separator size="4" my="3" />
      <Text size="1" weight="bold" color="gray" style={{ textTransform: "uppercase", letterSpacing: "0.08em" }}>
        Edge types
      </Text>
      <Flex direction="column" gap="2" mt="2">
        {(Object.keys(EDGE_STYLES) as EdgeClass[]).map((kind) => (
          <Flex key={kind} align="center" gap="2">
            <svg width="28" height="10" style={{ flexShrink: 0 }} aria-hidden="true">
              <line
                x1="1"
                y1="5"
                x2="26"
                y2="5"
                stroke={EDGE_STYLES[kind].color}
                strokeWidth="1.5"
                strokeDasharray={EDGE_STYLES[kind].dash}
              />
            </svg>
            <Box>
              <Text size="1" weight="bold" as="div">{EDGE_LEGEND_LABEL[kind]}</Text>
              <Text size="1" color="gray" as="div">{EDGE_STYLES[kind].label}</Text>
            </Box>
          </Flex>
        ))}
      </Flex>

      <Separator size="4" my="3" />
      <Text size="1" weight="bold" color="gray" style={{ textTransform: "uppercase", letterSpacing: "0.08em" }}>
        Icons
      </Text>
      <Flex direction="column" gap="2" mt="2">
        <Flex align="center" gap="2">
          <Repeat2 size={16} color="var(--orange-9)" style={{ flexShrink: 0, margin: 3 }} />
          <Box>
            <Text size="1" weight="bold" as="div">Loop</Text>
            <Text size="1" color="gray" as="div">Executes inside a source-code loop.</Text>
          </Box>
        </Flex>
        <Flex align="center" gap="2">
          <svg width="22" height="22" viewBox="0 0 22 22" style={{ flexShrink: 0 }} aria-hidden="true">
            <circle cx="11" cy="11" r="7" fill={NODE_STYLES.call.fill} stroke={NODE_STYLES.call.stroke} />
            <text x="11" y="14.5" textAnchor="middle" fontSize="12" fill={NODE_STYLES.call.stroke}>↻</text>
          </svg>
          <Box>
            <Text size="1" weight="bold" as="div">Recursive cutoff</Text>
            <Text size="1" color="gray" as="div">A call back into the active method.</Text>
          </Box>
        </Flex>
      </Flex>
    </Card>
  );
}
