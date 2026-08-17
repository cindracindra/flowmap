import { useCallback, useMemo, useState } from "react";
import { Box, Flex, Text, Heading, Badge, IconButton, ScrollArea, Separator } from "@radix-ui/themes";
import {
  ChevronLeft,
  Layers,
  FileText,
  Boxes,
  GitBranch,
} from "lucide-react";

import { OPERATIONS_BY_TOPIC, OPSEQ_VISUALISATIONS, TOPICS } from "../data/graph";
import type { TopicCluster, TopicOperation } from "../types/topics";
import { topicLabel, isUnnamed, NOISE_LABEL } from "../lib/topics";
import { MONO } from "../lib/ui";
import AnchoredGraphView from "./AnchoredGraphView";

// ── Topic discovery view ─────────────────────────────────────────────────
// Mode 1 output, read top-down: the clusters the corpus falls into, what
// each one contains, and (on a double click) one cluster on its own. The
// The topic and operation pickers stay full-width. Once an opseq is chosen,
// AnchoredGraphView supplies the same Explore/Operation panel as it does in
// the standalone anchored flow.

// ── Topic list ───────────────────────────────────────────────────────────

function TopicRow({
  topic,
  selected,
  onSelect,
}: {
  topic: TopicCluster;
  selected: boolean;
  onSelect: () => void;
}) {
  const isNoise = topic.label === NOISE_LABEL;
  const hasLlmLabel = Boolean(topic.llm_label?.trim());
  const unnamed = isUnnamed(topic);
  const operations = OPERATIONS_BY_TOPIC[String(topic.label)] ?? [];

  return (
    <button
      onClick={onSelect}
      title="Open assigned operations"
      aria-pressed={selected}
      style={{
        all: "unset",
        boxSizing: "border-box",
        display: "block",
        width: "100%",
        padding: "10px 12px",
        borderRadius: 6,
        cursor: "pointer",
        userSelect: "none",
        background: selected ? "var(--accent-a3)" : "var(--canvas-card)",
        border: `1px solid ${selected ? "var(--accent-a7)" : "var(--canvas-border)"}`,
      }}
    >
      <Flex align="center" gap="2">
        <Box style={{ color: isNoise ? "var(--gray-9)" : "var(--accent-9)", display: "flex" }}>
          {isNoise ? <Boxes size={13} /> : <Layers size={13} />}
        </Box>
        <Text
          size="2"
          weight="medium"
          truncate
          style={{ flex: 1, color: selected ? "var(--accent-11)" : "var(--canvas-foreground)" }}
        >
          {topicLabel(topic)}
        </Text>
        {hasLlmLabel && (
          <Badge size="1" variant="outline" color="violet">
            LLM label
          </Badge>
        )}
        {unnamed && !isNoise && (
          <Badge size="1" variant="outline" color="gray">
            unlabelled
          </Badge>
        )}
        <Badge size="1" variant="soft" color={isNoise ? "gray" : "teal"} style={{ fontFamily: MONO }}>
          {operations.length}
        </Badge>
      </Flex>
      <Text
        size="1"
        color="gray"
        truncate
        style={{ display: "block", fontFamily: MONO, marginTop: 4, paddingLeft: 21 }}
      >
        {operations.map((operation) => operation.label).join(" · ") || "No operation sequences"}
      </Text>
    </button>
  );
}

function TopicList({
  selectedLabel,
  onSelect,
}: {
  selectedLabel: number | null;
  onSelect: (label: number) => void;
}) {
  return (
    <ScrollArea style={{ height: "100%" }}>
      <Box p="4" style={{ maxWidth: 720, margin: "0 auto" }}>
        <Heading size="3">Topics</Heading>
        <Text as="p" size="1" color="gray" mt="1">
          {Object.keys(OPSEQ_VISUALISATIONS).length} operation sequences across{" "}
          {TOPICS.filter((topic) => topic.label !== NOISE_LABEL).length} topics. Click a topic to see
          its assigned operation sequences.
        </Text>
        <Flex direction="column" gap="2" mt="3">
          {TOPICS.map((topic) => (
            <TopicRow
              key={topic.label}
              topic={topic}
              selected={topic.label === selectedLabel}
              onSelect={() => onSelect(topic.label)}
            />
          ))}
          {TOPICS.length === 0 && (
            <Text size="1" color="gray" align="center" mt="4" as="p">
              No topics in this run.
            </Text>
          )}
        </Flex>
      </Box>
    </ScrollArea>
  );
}

// ── Single-topic view (double click) ─────────────────────────────────────
// A topic is useful because it groups operations as well as classes. Its
// drill-down therefore answers "what can this topic do?" with the opseqs
// assigned by the backend, rather than repeating the class-member evidence
// already available in the right-hand panel.

function TopicDetailView({
  topic,
  onBack,
  onOpenOperation,
}: {
  topic: TopicCluster;
  onBack: () => void;
  onOpenOperation: (operation: TopicOperation) => void;
}) {
  const operations = OPERATIONS_BY_TOPIC[String(topic.label)] ?? [];

  return (
    <Flex direction="column" style={{ height: "100%" }}>
      <Flex
        align="center"
        gap="2"
        px="3"
        flexShrink="0"
        height="36px"
        style={{ borderBottom: "1px solid var(--gray-a5)", background: "var(--color-panel-solid)" }}
      >
        <IconButton size="1" variant="ghost" color="gray" onClick={onBack} title="Back to all topics">
          <ChevronLeft size={14} />
        </IconButton>
        <BreadcrumbButton onClick={onBack}>
          All Topics
        </BreadcrumbButton>
        <Text color="gray">/</Text>
        <Text size="1" weight="medium">
          {topicLabel(topic)}
        </Text>
        <Badge size="1" variant="soft" color="gray" ml="auto" style={{ fontFamily: MONO }}>
          cluster {topic.label}
        </Badge>
      </Flex>

      <ScrollArea style={{ flex: 1 }}>
        <Box p="4" style={{ maxWidth: 720, margin: "0 auto" }}>
          <Heading size="5">{topicLabel(topic)}</Heading>
          <Text as="p" size="1" color="gray" mt="1">
            {operations.length} assigned {operations.length === 1 ? "operation" : "operations"}
          </Text>

          <Separator size="4" my="4" />

          <Text size="1" weight="bold" color="gray" style={{ textTransform: "uppercase", letterSpacing: "0.08em" }}>
            Operations
          </Text>
          <Flex direction="column" gap="2" mt="2">
            {operations.map((operation) => (
              <button
                key={operation.id}
                onClick={() => onOpenOperation(operation)}
                title="Open operation graph"
                aria-label={`Open graph for ${operation.label}`}
                style={{
                  all: "unset",
                  boxSizing: "border-box",
                  display: "block",
                  width: "100%",
                  cursor: "pointer",
                  padding: "8px 12px",
                  border: "1px solid var(--canvas-border)",
                  borderRadius: 6,
                  background: "var(--canvas-card)",
                }}
              >
                <Flex align="center" gap="2">
                  <GitBranch size={13} color="var(--accent-9)" />
                  <Text size="2" weight="medium" style={{ flex: 1 }}>
                    {operation.label}
                  </Text>
                </Flex>
                <Text size="1" color="gray" mt="1" style={{ display: "block", fontFamily: MONO, paddingLeft: 21 }}>
                  {operation.id}
                </Text>
              </button>
            ))}
            {operations.length === 0 && (
              <Text size="1" color="gray" as="p">
                No operations were assigned to this topic.
              </Text>
            )}
          </Flex>

          {topic.readme_paths.length > 0 && (
            <>
              <Separator size="4" my="4" />
              <Text size="1" weight="bold" color="gray" style={{ textTransform: "uppercase", letterSpacing: "0.08em" }}>
                Docs
              </Text>
              <Flex direction="column" gap="1" mt="2">
                {topic.readme_paths.map((path) => (
                  <Flex key={path} align="center" gap="2">
                    <FileText size={12} color="var(--gray-9)" />
                    <Text size="1" style={{ fontFamily: MONO }}>
                      {path}
                    </Text>
                  </Flex>
                ))}
              </Flex>
            </>
          )}
        </Box>
      </ScrollArea>
    </Flex>
  );
}

// ── View ─────────────────────────────────────────────────────────────────

function BreadcrumbButton({ children, onClick }: { children: string; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={`Go to ${children}`}
      style={{
        all: "unset",
        cursor: "pointer",
        color: "var(--gray-11)",
        fontSize: 12,
      }}
    >
      {children}
    </button>
  );
}

export default function TopicDiscoveryView() {
  const [selectedLabel, setSelectedLabel] = useState<number | null>(null);
  const [selectedOperation, setSelectedOperation] = useState<TopicOperation | null>(null);
  const [displayedOperationLabel, setDisplayedOperationLabel] = useState<string | null>(null);

  const byLabel = useMemo(() => new Map(TOPICS.map((t) => [t.label, t])), []);
  const selectedTopic = selectedLabel !== null ? (byLabel.get(selectedLabel) ?? null) : null;

  const handleSelect = useCallback((label: number) => {
    setSelectedLabel(label);
    setSelectedOperation(null);
    setDisplayedOperationLabel(null);
  }, []);

  const handleOpenOperation = useCallback((operation: TopicOperation) => {
    setSelectedOperation(operation);
    setDisplayedOperationLabel(operation.label);
  }, []);

  const opseqCount = Object.keys(OPSEQ_VISUALISATIONS).length;
  const noiseCount = OPERATIONS_BY_TOPIC[String(NOISE_LABEL)]?.length ?? 0;
  const topicCount = TOPICS.filter((topic) => topic.label !== NOISE_LABEL).length;

  return (
    <Flex direction="column" flexGrow="1" overflow="hidden" style={{ minHeight: 0 }}>
      <Flex flexGrow="1" style={{ minHeight: 0 }}>
        <Box
          position="relative"
          flexGrow="1"
          overflow="hidden"
          style={{ minWidth: 0, background: "var(--canvas-background)" }}
        >
          {selectedOperation ? (
            <Flex direction="column" style={{ height: "100%" }}>
              <Flex align="center" gap="2" px="3" flexShrink="0" height="36px" style={{ borderBottom: "1px solid var(--gray-a5)", background: "var(--color-panel-solid)" }}>
                <IconButton
                  size="1"
                  variant="ghost"
                  color="gray"
                  onClick={() => {
                    setSelectedOperation(null);
                    setDisplayedOperationLabel(null);
                  }}
                  title="Back to operations"
                >
                  <ChevronLeft size={14} />
                </IconButton>
                <BreadcrumbButton
                  onClick={() => {
                    setSelectedLabel(null);
                    setSelectedOperation(null);
                    setDisplayedOperationLabel(null);
                  }}
                >
                  All Topics
                </BreadcrumbButton>
                <Text color="gray">/</Text>
                <BreadcrumbButton
                  onClick={() => {
                    setSelectedOperation(null);
                    setDisplayedOperationLabel(null);
                  }}
                >
                  {topicLabel(selectedTopic!)}
                </BreadcrumbButton>
                <Text color="gray">/</Text>
                <Text size="1" weight="medium">{displayedOperationLabel ?? selectedOperation.label}</Text>
              </Flex>
              <Box flexGrow="1" height="100%" style={{ minHeight: 0 }}>
                {OPSEQ_VISUALISATIONS[selectedOperation.id] ? (
                  <AnchoredGraphView
                    {...OPSEQ_VISUALISATIONS[selectedOperation.id]}
                    initialPanelTab="operation"
                    onOpseqChange={(choice) => setDisplayedOperationLabel(choice.label)}
                  />
                ) : (
                  <Flex align="center" justify="center" height="100%">
                    <Text size="1" color="gray">No static graph is available for this operation.</Text>
                  </Flex>
                )}
              </Box>
            </Flex>
          ) : selectedTopic ? (
            <TopicDetailView
              topic={selectedTopic}
              onBack={() => setSelectedLabel(null)}
              onOpenOperation={handleOpenOperation}
            />
          ) : (
            <TopicList selectedLabel={selectedLabel} onSelect={handleSelect} />
          )}
        </Box>
      </Flex>

      {/* Status bar */}
      <Flex
        align="center"
        gap="3"
        px="4"
        flexShrink="0"
        height="24px"
        style={{ borderTop: "1px solid var(--gray-a5)", background: "var(--color-panel-solid)" }}
      >
        <Flex align="center" gap="2">
          <Box style={{ width: 6, height: 6, borderRadius: "50%", background: "var(--teal-9)" }} />
          <Text size="1" color="gray">
            topic clusters
          </Text>
        </Flex>
        <Text size="1" color="gray">
          ·
        </Text>
        <Text size="1" color="gray" style={{ fontFamily: MONO }}>
          {topicCount} topics
        </Text>
        <Text size="1" color="gray">
          ·
        </Text>
        <Text size="1" color="gray" style={{ fontFamily: MONO }}>
          {opseqCount} opseqs
        </Text>
        <Text size="1" color="gray">
          ·
        </Text>
        <Text size="1" color="gray" style={{ fontFamily: MONO }}>
          {noiseCount} unclustered
        </Text>
        <Flex align="center" gap="2" ml="auto">
          {selectedTopic && (
            <Text size="1" color="amber" style={{ fontFamily: MONO }}>
              {topicLabel(selectedTopic)}
            </Text>
          )}
        </Flex>
      </Flex>
    </Flex>
  );
}
