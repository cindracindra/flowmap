import { Badge, Box, Card, Flex, Heading, ScrollArea, Separator, Text } from "@radix-ui/themes";
import {
  BookOpen,
  Braces,
  ChevronRight,
  CircleDot,
  Compass,
  GitFork,
  Info,
  Layers3,
  MousePointer2,
  PanelLeft,
  PanelRight,
  Repeat2,
  Route,
  Workflow,
  ZoomIn,
} from "lucide-react";

import { MONO } from "../lib/ui";

const sectionLabelStyle = {
  textTransform: "uppercase" as const,
  letterSpacing: "0.09em",
};

function SectionTitle({ eyebrow, title, description }: { eyebrow: string; title: string; description?: string }) {
  return (
    <Box mb="3">
      <Text size="1" weight="bold" color="gray" style={sectionLabelStyle}>{eyebrow}</Text>
      <Heading size="5" mt="1">{title}</Heading>
      {description && <Text as="p" size="2" color="gray" mt="1" style={{ maxWidth: 720, lineHeight: 1.55 }}>{description}</Text>}
    </Box>
  );
}

function GuideCard({ icon, title, children }: { icon: React.ReactNode; title: string; children: React.ReactNode }) {
  return (
    <Card size="2" style={{ height: "100%" }}>
      <Flex align="center" gap="2" mb="2">
        <Box style={{ display: "flex", color: "var(--accent-9)" }}>{icon}</Box>
        <Text size="2" weight="bold">{title}</Text>
      </Flex>
      <Text as="div" size="1" color="gray" style={{ lineHeight: 1.55 }}>{children}</Text>
    </Card>
  );
}

function Step({ number, title, icon, view, children }: { number: number; title: string; icon: React.ReactNode; view: string; children: React.ReactNode }) {
  return (
    <Flex direction="column" gap="3" style={{ minWidth: 0 }}>
      <Flex gap="2" align="center">
        <Flex align="center" justify="center" flexShrink="0" width="24px" height="24px"
          style={{ borderRadius: "50%", background: "var(--accent-a4)", color: "var(--accent-11)", fontFamily: MONO, fontSize: 11, fontWeight: 700 }}>
          {number}
        </Flex>
        <Text size="2" weight="bold">{title}</Text>
      </Flex>
      <GuideCard icon={icon} title={view}>{children}</GuideCard>
    </Flex>
  );
}

function Term({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <Flex gap="2" align="start">
      <Badge size="1" variant="soft" color="gray" style={{ flexShrink: 0, fontFamily: MONO }}>{label}</Badge>
      <Text size="1" color="gray" style={{ lineHeight: 1.5 }}>{children}</Text>
    </Flex>
  );
}

export default function UserGuideView() {
  return (
    <ScrollArea style={{ height: "100%", background: "var(--canvas-background)" }}>
      <Box px="5" py="5" style={{ width: "100%", maxWidth: 1040, margin: "0 auto" }}>
        <Flex align="start" gap="3">
          <Flex align="center" justify="center" width="38px" height="38px" flexShrink="0"
            style={{ borderRadius: 8, background: "var(--accent-a3)", border: "1px solid var(--accent-a6)", color: "var(--accent-9)" }}>
            <BookOpen size={19} />
          </Flex>
          <Box>
            <Heading size="7">FlowMap user guide</Heading>
            <Text as="p" size="2" color="gray" mt="1" style={{ maxWidth: 760, lineHeight: 1.6 }}>
              FlowMap turns Java control flow into explorable operation sequences. Use it to move from a high-level topic,
              through an operation, down to the methods and statements that can execute.
            </Text>
          </Box>
        </Flex>

        <Separator size="4" my="5" />

        <SectionTitle eyebrow="Start here" title="A useful way through the site"
          description="Begin with the high-level behaviour, then follow it into the implementation." />
        <Box mb="6" style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(300px, 1fr))", gap: 20 }}>
          <Step number={1} title="Find a relevant topic and drill down" icon={<Compass size={16} />} view="Topic Discovery">
            Groups operation sequences by what they do. Topic cards show their label and operation count; opening a topic lists
            its assigned operations and supporting README paths. Choosing an operation opens its expandable graph.
          </Step>
          <Step number={2} title="Inspect implementation detail" icon={<Workflow size={16} />} view="Expandable Graph">
            Starts with one method’s local flow and attaches called method bodies only when you request them. This keeps context
            visible while letting you progressively reveal implementation detail.
          </Step>
        </Box>

        <SectionTitle eyebrow="Workspace" title="Reading the Graph"
          description="The workspace is split into navigation, execution phases, the graph canvas, and node details." />
        <Box mb="6" style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: 12 }}>
          <GuideCard icon={<PanelLeft size={16} />} title="Left panel">
            <strong>Explore</strong> browses methods by source folder and file; <strong>Operations</strong> browses operation
            sequences. Search filters the active tree. Selecting a method resets expansion and shows that method’s local flow.
            The chevron collapses or restores the panel.
          </GuideCard>
          <GuideCard icon={<Layers3 size={16} />} title="Execution phases">
            Coloured blocks group adjacent work into meaningful, method-local stages. Their labels summarise intent rather than
            Java syntax. Nested indentation indicates a phase belonging to an expanded callee. Click a phase to scroll to it.
          </GuideCard>
          <GuideCard icon={<PanelRight size={16} />} title="Right panel">
            Select a node to see its operation text, owning method, source file and line, method-local phase, exit meaning, and
            loop membership. It also identifies recursion cutoffs. The chevron collapses or restores the panel.
          </GuideCard>
          <GuideCard icon={<Info size={16} />} title="Legend">
            The global <strong>Legend</strong> button explains node shapes, edge styles, loops, and recursive calls. Use it when
            distinguishing sequence, invocation, return, and inferred fallback edges, or entry, call, leaf, and exit nodes.
          </GuideCard>
        </Box>

        <SectionTitle eyebrow="Interactions" title="Explore without losing context" />
        <Box mb="6" style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(250px, 1fr))", gap: 12 }}>
          <GuideCard icon={<MousePointer2 size={16} />} title="Select and inspect">
            Click any node to highlight it and populate Node details. Hover a node for its full label, node type, and source
            location. The counts above the canvas link to callers and operation sequences that contain the current method.
          </GuideCard>
          <GuideCard icon={<ZoomIn size={16} />} title="Expand a call">
            A <strong>+</strong> inside a call means its target can be revealed. Click it to insert the callee’s flow directly
            below the call; <strong>−</strong> collapses it. “N phases inside” summarises retained calls whose internals are hidden.
          </GuideCard>
          <GuideCard icon={<Repeat2 size={16} />} title="Loops and recursion">
            The loop icon marks work executed inside a source-code loop; details show the loop kind and condition. A <strong>↻</strong>
            marks a recursive call that is deliberately not expanded again, preventing an infinite graph.
          </GuideCard>
        </Box>

        <SectionTitle eyebrow="Control flow" title="Branches, exceptions, and dispatch"
          description="Only one alternative is shown at a time. Select another labelled pill to compare the possible route through the same code." />
        <Flex direction="column" gap="3" mb="6">
          <Card size="2">
            <Flex gap="3" align="start">
              <GitFork size={17} color="var(--panel-conditional)" style={{ flexShrink: 0, marginTop: 2 }} />
              <Box>
                <Text size="2" weight="bold" as="div">If / else and switch</Text>
                <Text size="1" color="gray" as="p" mt="1" style={{ lineHeight: 1.55 }}>
                  An <strong>IF</strong> or <strong>SWITCH</strong> group represents mutually exclusive paths. Pills contain the
                  source condition, case, or <strong>else</strong> label. Selecting a pill replaces the visible arm; it does not mean
                  that FlowMap executed that arm at runtime.
                </Text>
              </Box>
            </Flex>
          </Card>
          <Card size="2">
            <Flex gap="3" align="start">
              <Braces size={17} color="var(--panel-conditional)" style={{ flexShrink: 0, marginTop: 2 }} />
              <Box>
                <Text size="2" weight="bold" as="div">Try / catch</Text>
                <Text size="1" color="gray" as="p" mt="1" style={{ lineHeight: 1.55 }}>
                  A <strong>TRY</strong> group switches between normal completion and compatible catch handlers. Its selector appears
                  after the protected work because the outcome is determined there. Catch labels show the handled exception type;
                  code before or after the selectable region is shared flow.
                </Text>
              </Box>
            </Flex>
          </Card>
          <Card size="2">
            <Flex gap="3" align="start">
              <Route size={17} color="var(--panel-polymorphic)" style={{ flexShrink: 0, marginTop: 2 }} />
              <Box>
                <Text size="2" weight="bold" as="div">Dynamic dispatch</Text>
                <Text size="1" color="gray" as="p" mt="1" style={{ lineHeight: 1.55 }}>
                  When an interface or overridable method has several possible implementations, a <strong>dispatch</strong> group
                  lists the possible runtime receiver types. Pick a class-labelled arm to inspect that implementation. These are
                  static possibilities found by the analysis, not proof of which class occurred in one execution.
                </Text>
              </Box>
            </Flex>
          </Card>
        </Flex>

        <SectionTitle eyebrow="Terminology" title="A few distinctions that help" />
        <Flex direction="column" gap="3" mb="5">
          <Term label="operation sequence">An analysed unit of application behaviour. It may cross several methods and is what topics group.</Term>
          <Term label="method">A Java method definition. The expandable canvas can focus on one method while preserving links to operations that use it.</Term>
          <Term label="phase">A semantic stage within a method, inferred from related work. It is not a thread, timing measurement, or runtime trace span.</Term>
          <Term label="leaf">An external or unresolved operation whose internal control flow is not available to expand.</Term>
          <Term label="fallback">An inferred continuation used when the analysed callee has no direct path back to the caller’s next visible node.</Term>
        </Flex>

        <Flex align="center" gap="2" p="3" style={{ borderRadius: 7, background: "var(--accent-a2)", border: "1px solid var(--accent-a5)" }}>
          <CircleDot size={14} color="var(--accent-9)" />
          <Text size="1" color="gray">
            Tip: begin broad and reveal detail only as needed. The current branch and dispatch choices are presentation choices, not recorded runtime evidence.
          </Text>
          <ChevronRight size={13} color="var(--accent-9)" style={{ marginLeft: "auto", flexShrink: 0 }} />
        </Flex>
      </Box>
    </ScrollArea>
  );
}
