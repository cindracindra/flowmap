import { useState } from "react";
import { Flex, Text, Button } from "@radix-ui/themes";
import { GitBranch, Info, Compass, Waypoints } from "lucide-react";

import AnchoredGraphView, { GraphLegend } from "./views/AnchoredGraphView";
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
  const [legendOpen, setLegendOpen] = useState(false);

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
        </Flex>
        <div style={{ position: "relative" }}>
          <Button
            size="1"
            variant={legendOpen ? "solid" : "soft"}
            color="gray"
            aria-label={legendOpen ? "Hide legend" : "Show legend"}
            aria-expanded={legendOpen}
            onClick={() => setLegendOpen((open) => !open)}
          >
            <Info size={13} /> Legend
          </Button>
          {legendOpen && (
            <div style={{ position: "absolute", top: 32, right: 0, zIndex: 20 }}>
              <GraphLegend />
            </div>
          )}
        </div>
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
          <AnchoredGraphView />
        ) : <TopicDiscoveryView />}
      </Flex>
    </Flex>
  );
}
