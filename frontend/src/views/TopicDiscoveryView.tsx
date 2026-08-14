import { useCallback, useMemo, useState } from "react";
import { Box, Flex, Text, Heading, Badge, IconButton, ScrollArea, Separator } from "@radix-ui/themes";
import {
  ChevronLeft,
  ChevronRight,
  Minimize2,
  Layers,
  FileText,
  Boxes,
  GitBranch,
} from "lucide-react";

import { OPERATIONS_BY_TOPIC, OPSEQ_VISUALISATIONS, TOPICS } from "../data/graph";
import type { TopicCluster, TopicOperation } from "../types/topics";
import { topicLabel, isUnnamed, splitClassFullName, NOISE_LABEL } from "../lib/topics";
import { MONO } from "../lib/ui";
import AnchoredGraphView from "./AnchoredGraphView";

// ── Topic discovery view ─────────────────────────────────────────────────
// Mode 1 output, read top-down: the clusters the corpus falls into, what
// each one contains, and (on a double click) one cluster on its own. The
// left panel is deliberately empty -- its content is not decided yet, but
// the frame it will live in is, so the three-column shape matches the
// anchored graph view rather than being retrofitted onto it later.

// Members are listed package-first: a cluster is a claim about which
// classes belong together, and that claim is easiest to check against the
// package layout it cuts across.
function groupByPackage(fullNames: string[]): { pkg: string; classes: string[] }[] {
  const byPkg = new Map<string, string[]>();
  for (const fullName of fullNames) {
    const { pkg, shortName } = splitClassFullName(fullName);
    const bucket = byPkg.get(pkg);
    if (bucket) bucket.push(shortName);
    else byPkg.set(pkg, [shortName]);
  }
  return [...byPkg.entries()]
    .map(([pkg, classes]) => ({ pkg, classes: classes.sort() }))
    .sort((a, b) => a.pkg.localeCompare(b.pkg));
}

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
  const unnamed = isUnnamed(topic);

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
        {unnamed && (
          <Badge size="1" variant="outline" color="gray">
            unlabelled
          </Badge>
        )}
        <Badge size="1" variant="soft" color={isNoise ? "gray" : "teal"} style={{ fontFamily: MONO }}>
          {topic.member_full_names.length}
        </Badge>
      </Flex>
      <Text
        size="1"
        color="gray"
        truncate
        style={{ display: "block", fontFamily: MONO, marginTop: 4, paddingLeft: 21 }}
      >
        {topic.member_full_names
          .map((fullName) => splitClassFullName(fullName).shortName)
          .join(" · ")}
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
          {TOPICS.length} clusters over{" "}
          {TOPICS.reduce((n, t) => n + t.member_full_names.length, 0)} classes. Click a topic for its
          assigned operations.
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
        <Text size="1" color="gray">
          All topics
        </Text>
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

          {topic.statistical_terms.length > 0 && (
            <Flex gap="1" wrap="wrap" mt="3">
              {topic.statistical_terms.map((term) => (
                <Badge key={term} size="1" variant="soft" color="teal" style={{ fontFamily: MONO }}>
                  {term}
                </Badge>
              ))}
            </Flex>
          )}

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

// ── Right detail panel ───────────────────────────────────────────────────

function TopicClassesPanel({ topic }: { topic: TopicCluster }) {
  const groups = useMemo(() => groupByPackage(topic.member_full_names), [topic]);

  return (
    <ScrollArea style={{ height: "100%" }}>
      <Flex direction="column">
        <Box p="3" style={{ borderBottom: "1px solid var(--gray-a5)" }}>
          <Heading size="3">{topicLabel(topic)}</Heading>
          <Flex gap="2" mt="2" wrap="wrap">
            <Badge size="1" variant="soft" color="gray" style={{ fontFamily: MONO }}>
              cluster {topic.label}
            </Badge>
            {/* Naming the source matters: an LLM label is a summary of the
                cluster, a c-TF-IDF term is a measurement of it, and the
                fallback is neither. */}
            <Badge size="1" variant="outline" color="gray">
              {topic.llm_label?.trim()
                ? "llm label"
                : topic.statistical_terms.some((t) => t.trim())
                  ? "top term"
                  : "unlabelled"}
            </Badge>
          </Flex>
        </Box>

        <Box p="3">
          <Text size="1" weight="bold" color="gray" style={{ textTransform: "uppercase", letterSpacing: "0.08em" }}>
            Members ({topic.member_full_names.length})
          </Text>
          <Flex direction="column" gap="3" mt="2">
            {groups.map((group) => (
              <Box key={group.pkg}>
                <Text size="1" color="gray" truncate style={{ fontFamily: MONO, display: "block" }}>
                  {group.pkg}
                </Text>
                <Flex direction="column" gap="1" mt="1">
                  {group.classes.map((name) => (
                    <Text key={name} size="1" truncate style={{ fontFamily: MONO, paddingLeft: 8 }}>
                      {name}
                    </Text>
                  ))}
                </Flex>
              </Box>
            ))}
          </Flex>
        </Box>

      </Flex>
    </ScrollArea>
  );
}

// ── View ─────────────────────────────────────────────────────────────────

export default function TopicDiscoveryView() {
  const [selectedLabel, setSelectedLabel] = useState<number | null>(null);
  const [selectedOperation, setSelectedOperation] = useState<TopicOperation | null>(null);
  const [leftOpen, setLeftOpen] = useState(true);

  const byLabel = useMemo(() => new Map(TOPICS.map((t) => [t.label, t])), []);
  const selectedTopic = selectedLabel !== null ? (byLabel.get(selectedLabel) ?? null) : null;

  const handleSelect = useCallback((label: number) => {
    setSelectedLabel(label);
    setSelectedOperation(null);
  }, []);

  const classCount = useMemo(
    () => TOPICS.reduce((n, t) => n + t.member_full_names.length, 0),
    [],
  );
  const noiseCount = byLabel.get(NOISE_LABEL)?.member_full_names.length ?? 0;

  return (
    <Flex direction="column" flexGrow="1" overflow="hidden" style={{ minHeight: 0 }}>
      <Flex flexGrow="1" style={{ minHeight: 0 }}>
        {/* The selected topic's class membership is supporting evidence for
            its operation list, so it stays visible without taking over the
            main reading path. */}
        {leftOpen && (
          <Box
            flexShrink="0"
            width="240px"
            style={{ borderRight: "1px solid var(--gray-a5)", background: "var(--color-panel-solid)" }}
          >
            <Flex direction="column" style={{ height: "100%" }}>
              <Flex
                align="center"
                justify="between"
                px="3"
                flexShrink="0"
                height="36px"
                style={{ borderBottom: "1px solid var(--gray-a5)" }}
              >
                <Text size="1" weight="medium" color="gray">
                  Topics
                </Text>
                <IconButton size="1" variant="ghost" color="gray" onClick={() => setLeftOpen(false)}>
                  <Minimize2 size={13} />
                </IconButton>
              </Flex>
              {selectedTopic ? (
                <Box flexGrow="1" style={{ minHeight: 0 }}>
                  <TopicClassesPanel topic={selectedTopic} />
                </Box>
              ) : (
                <Flex align="center" justify="center" flexGrow="1" p="4">
                  <Text size="1" color="gray" align="center">
                    Select a topic to see its classes.
                  </Text>
                </Flex>
              )}
            </Flex>
          </Box>
        )}

        {/* Collapsed left panel keeps a rail rather than floating a button
            over the content: unlike the graph canvas, everything here is
            text starting at the top-left corner. */}
        {!leftOpen && (
          <Flex
            direction="column"
            align="center"
            flexShrink="0"
            width="32px"
            pt="2"
            style={{ borderRight: "1px solid var(--gray-a5)", background: "var(--color-panel-solid)" }}
          >
            <IconButton
              size="1"
              variant="ghost"
              color="gray"
              onClick={() => setLeftOpen(true)}
              title="Show panel"
            >
              <ChevronRight size={14} />
            </IconButton>
          </Flex>
        )}

        {/* Centre */}
        <Box
          position="relative"
          flexGrow="1"
          overflow="hidden"
          style={{ minWidth: 0, background: "var(--canvas-background)" }}
        >
          {selectedOperation ? (
            <Flex direction="column" style={{ height: "100%" }}>
              <Flex align="center" gap="2" px="3" flexShrink="0" height="36px" style={{ borderBottom: "1px solid var(--gray-a5)", background: "var(--color-panel-solid)" }}>
                <IconButton size="1" variant="ghost" color="gray" onClick={() => setSelectedOperation(null)} title="Back to operations">
                  <ChevronLeft size={14} />
                </IconButton>
                <Text size="1" color="gray">{topicLabel(selectedTopic!)}</Text>
                <Text color="gray">/</Text>
                <Text size="1" weight="medium">{selectedOperation.label}</Text>
              </Flex>
              <Box flexGrow="1" height="100%" style={{ minHeight: 0 }}>
                {OPSEQ_VISUALISATIONS[selectedOperation.id] ? (
                  <AnchoredGraphView {...OPSEQ_VISUALISATIONS[selectedOperation.id]} />
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
              onOpenOperation={setSelectedOperation}
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
          {TOPICS.length} topics
        </Text>
        <Text size="1" color="gray">
          ·
        </Text>
        <Text size="1" color="gray" style={{ fontFamily: MONO }}>
          {classCount} classes
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
