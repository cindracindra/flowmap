import { useState } from "react";
import { Box, Flex, Text, Badge, Button, IconButton } from "@radix-ui/themes";
import { GitBranch, Upload, Settings, Compass, Waypoints } from "lucide-react";

import { GRAPH } from "./data/graph";
import { shortLabel } from "./lib/graph";
import { MONO } from "./lib/ui";
import AnchoredGraphView from "./views/AnchoredGraphView";
import TopicDiscoveryView from "./views/TopicDiscoveryView";

// ── App shell ────────────────────────────────────────────────────────────
// Header identifies the trace; the sub-header switches between the two
// views of it. Each view owns everything below the sub-header, including
// its own side panels and status bar -- they answer different questions and
// share no canvas state.

type View = "topics" | "anchored";

const VIEWS: { id: View; label: string; icon: typeof Compass; hint: string }[] = [
  { id: "topics", label: "Topic discovery", icon: Compass, hint: "what this trace is about" },
  { id: "anchored", label: "Anchored graph", icon: Waypoints, hint: "the flattened control flow" },
];

function ViewTab({
  view,
  active,
  onSelect,
}: {
  view: (typeof VIEWS)[number];
  active: boolean;
  onSelect: () => void;
}) {
  const Icon = view.icon;
  return (
    <button
      onClick={onSelect}
      title={view.hint}
      aria-current={active ? "page" : undefined}
      style={{
        all: "unset",
        boxSizing: "border-box",
        display: "flex",
        alignItems: "center",
        gap: 6,
        height: "100%",
        padding: "0 12px",
        cursor: "pointer",
        color: active ? "var(--accent-11)" : "var(--gray-10)",
        // The underline is the state marker, so the active tab is still
        // legible without relying on the colour shift alone.
        boxShadow: active ? "inset 0 -2px 0 0 var(--accent-9)" : "none",
      }}
    >
      <Icon size={13} />
      <Text size="1" weight={active ? "medium" : "regular"}>
        {view.label}
      </Text>
    </button>
  );
}

export default function App() {
  const [view, setView] = useState<View>("anchored");

  return (
    <Flex
      direction="column"
      width="100vw"
      height="100vh"
      overflow="hidden"
      style={{ background: "var(--color-background)" }}
    >
      {/* Header */}
      <Flex
        align="center"
        justify="between"
        px="4"
        flexShrink="0"
        height="40px"
        style={{ borderBottom: "1px solid var(--gray-a5)", background: "var(--color-panel-solid)" }}
      >
        <Flex align="center" gap="3">
          <Flex align="center" gap="2">
            <Flex
              align="center"
              justify="center"
              width="20px"
              height="20px"
              style={{
                borderRadius: 4,
                background: "var(--accent-a3)",
                border: "1px solid var(--accent-a6)",
              }}
            >
              <GitBranch size={12} color="var(--accent-9)" />
            </Flex>
            <Text weight="bold" size="2">
              FlowMap
            </Text>
          </Flex>
          <Text color="gray">·</Text>
          <Text size="1" color="gray" style={{ fontFamily: MONO }}>
            java_project
          </Text>
          <Text color="gray">·</Text>
          <Badge color="grass" variant="soft" style={{ fontFamily: MONO }}>
            {GRAPH.entryPoint ? shortLabel(GRAPH.entryPoint) : "no anchor"}
          </Badge>
        </Flex>
        <Flex align="center" gap="2">
          <Button size="1" variant="soft" color="amber">
            <Upload size={12} /> Upload
          </Button>
          <IconButton size="1" variant="ghost" color="gray">
            <Settings size={14} />
          </IconButton>
        </Flex>
      </Flex>

      {/* Sub-header: view switcher */}
      <Flex
        align="center"
        px="2"
        flexShrink="0"
        height="34px"
        style={{ borderBottom: "1px solid var(--gray-a5)", background: "var(--color-panel-solid)" }}
      >
        <Flex asChild align="center" height="100%">
          <nav aria-label="View">
          {VIEWS.map((v) => (
            <ViewTab key={v.id} view={v} active={view === v.id} onSelect={() => setView(v.id)} />
          ))}
          </nav>
        </Flex>
      </Flex>

      {/* Active view. Both are mounted-on-demand: the graph view holds pan,
          zoom and arm selection, and remounting it resets those, so switch
          away and back deliberately. */}
      <Flex direction="column" flexGrow="1" style={{ minHeight: 0 }}>
        {view === "anchored" ? (
          GRAPH.rootId ? (
            <AnchoredGraphView />
          ) : (
            <Flex align="center" justify="center" flexGrow="1" style={{ background: "var(--canvas-background)" }}>
              <Box style={{ textAlign: "center" }}>
                <Text size="2" weight="medium">No anchored graph selected</Text>
                <Text as="p" size="1" color="gray" mt="2">
                  Set ANCHOR_NAME and regenerate the CFG artifacts to show a top-level trace.
                </Text>
              </Box>
            </Flex>
          )
        ) : <TopicDiscoveryView />}
      </Flex>
    </Flex>
  );
}
