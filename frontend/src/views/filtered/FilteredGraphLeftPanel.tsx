import { useMemo, useState } from "react";
import { Box, Flex, IconButton, ScrollArea, Text, TextField } from "@radix-ui/themes";
import { ChevronDown, ChevronLeft, ChevronRight, FileCode2, FolderOpen, Hash, ListTree, Search } from "lucide-react";

import { opseqLabel } from "../../data/operationLabels";
import { shortLabel } from "../../lib/graph";
import { MONO } from "../../lib/ui";
import type { GraphBundle } from "../../types/filteredGraph";

type PanelTab = "explore" | "operations";
type LeafKind = "method" | "operation";

interface TreeItem {
  name: string;
  kind: "folder" | "file" | "leaf";
  id?: string;
  fullName?: string;
  children?: TreeItem[];
}

interface TreeEntry {
  path: string;
  name: string;
  id: string;
  fullName?: string;
}

function methodShortName(fullName: string): string {
  const qualified = fullName.split(":", 1)[0];
  const name = qualified.split(".").pop() ?? qualified;
  return name === "<init>" ? "constructor()" : `${name}()`;
}

function sourcePath(methodFullName: string, sourceFile?: string): string {
  if (sourceFile && !sourceFile.startsWith("<")) return sourceFile;
  const qualified = methodFullName.split(":", 1)[0];
  const className = qualified.split(".").slice(0, -1).join(".");
  return `src/main/java/${className.replaceAll(".", "/")}.java`;
}

function compactFolderChains(items: TreeItem[]): TreeItem[] {
  return items.map((item) => {
    if (item.kind !== "folder") return item;
    let name = item.name;
    let children = item.children ?? [];
    while (children.length === 1 && children[0].kind === "folder") {
      name += `/${children[0].name}`;
      children = children[0].children ?? [];
    }
    return { ...item, name, children: compactFolderChains(children) };
  });
}

// Shared by both tabs so source grouping, compact folders and styling cannot drift.
function buildSourceTree(entries: TreeEntry[]): TreeItem[] {
  const root: TreeItem[] = [];
  for (const entry of [...entries].sort((a, b) => a.path.localeCompare(b.path) || a.name.localeCompare(b.name))) {
    const parts = entry.path.split("/").filter(Boolean);
    const fileName = parts.pop();
    if (!fileName) continue;
    let children = root;
    for (const folder of parts) {
      let item = children.find((candidate) => candidate.kind === "folder" && candidate.name === folder);
      if (!item) {
        item = { name: folder, kind: "folder", children: [] };
        children.push(item);
      }
      children = item.children!;
    }
    let file = children.find((candidate) => candidate.kind === "file" && candidate.name === fileName);
    if (!file) {
      file = { name: fileName, kind: "file", children: [] };
      children.push(file);
    }
    file.children!.push({ name: entry.name, kind: "leaf", id: entry.id, fullName: entry.fullName });
  }
  return compactFolderChains(root);
}

function filterTree(items: TreeItem[], query: string): TreeItem[] {
  if (!query) return items;
  return items.flatMap((item) => {
    if (item.name.toLowerCase().includes(query) || item.fullName?.toLowerCase().includes(query)) return [item];
    const children = filterTree(item.children ?? [], query);
    return children.length ? [{ ...item, children }] : [];
  });
}

function TreeRow({ item, depth, selectedId, leafKind, onSelect }: {
  item: TreeItem;
  depth: number;
  selectedId: string | null;
  leafKind: LeafKind;
  onSelect: (id: string) => void;
}) {
  const [open, setOpen] = useState(true);
  const branch = item.kind !== "leaf";
  const selected = item.id === selectedId;
  return (
    <Box>
      <button
        title={item.fullName ?? item.name}
        onClick={() => branch ? setOpen((value) => !value) : item.id && onSelect(item.id)}
        style={{
          all: "unset", boxSizing: "border-box", display: "flex", alignItems: "center", gap: 6,
          width: "100%", padding: "5px 8px", paddingLeft: 8 + depth * 14, cursor: "pointer",
          borderRadius: 4, color: selected ? "var(--accent-11)" : "var(--gray-11)",
          background: selected ? "var(--accent-a3)" : "transparent",
        }}
      >
        {branch ? (open ? <ChevronDown size={12} /> : <ChevronRight size={12} />) : <Box style={{ width: 12, flexShrink: 0 }} />}
        {item.kind === "folder" && <FolderOpen size={12} color="var(--amber-9)" />}
        {item.kind === "file" && <FileCode2 size={12} color="var(--teal-9)" />}
        {item.kind === "leaf" && (leafKind === "method" ? <Hash size={12} color="var(--gray-9)" /> : <ListTree size={12} color="var(--gray-9)" />)}
        <Text size="1" truncate style={{ fontFamily: MONO }}>{item.name}</Text>
      </button>
      {branch && open && item.children?.map((child, index) => (
        <TreeRow key={`${child.kind}:${child.name}:${index}`} item={child} depth={depth + 1} selectedId={selectedId} leafKind={leafKind} onSelect={onSelect} />
      ))}
    </Box>
  );
}

export interface FilteredGraphLeftPanelProps {
  bundle: GraphBundle;
  selectedOperationId: string | null;
  selectedMethodEntryId: string | null;
  onSelectOperation: (operationId: string) => void;
  onSelectMethod: (entryId: string) => void;
  onCollapse: () => void;
}

function TabButton({ active, children, onClick }: { active: boolean; children: React.ReactNode; onClick: () => void }) {
  return <button onClick={onClick} style={{ all: "unset", cursor: "pointer", flex: 1, padding: "9px 10px", textAlign: "center", fontSize: 12, color: active ? "var(--accent-11)" : "var(--gray-10)", boxShadow: active ? "inset 0 -2px 0 var(--accent-9)" : "none" }}>{children}</button>;
}

export default function FilteredGraphLeftPanel({ bundle, selectedOperationId, selectedMethodEntryId, onSelectOperation, onSelectMethod, onCollapse }: FilteredGraphLeftPanelProps) {
  const [tab, setTab] = useState<PanelTab>("explore");
  const [query, setQuery] = useState("");
  const normalized = query.trim().toLowerCase();
  const methodTree = useMemo(() => buildSourceTree(Object.values(bundle.methodsByEntryId).map((method) => ({
    path: sourcePath(method.methodFullName, method.entry.sourceFile), name: methodShortName(method.methodFullName), id: method.entryId, fullName: method.methodFullName,
  }))), [bundle]);
  const operationTree = useMemo(() => buildSourceTree(Object.values(bundle.operationsById).map((operation) => {
    const method = bundle.methodsByEntryId[operation.rootEntryId];
    const fullName = method?.methodFullName ?? operation.id;
    const label = opseqLabel(operation.id)
      ?? (operation.label ? shortLabel(operation.label) : methodShortName(fullName));
    return { path: sourcePath(fullName, method?.entry.sourceFile), name: label, id: operation.id, fullName: label };
  })), [bundle]);
  const tree = filterTree(tab === "explore" ? methodTree : operationTree, normalized);
  const selectedId = tab === "explore" ? selectedMethodEntryId : selectedOperationId;
  const select = tab === "explore" ? onSelectMethod : onSelectOperation;

  return (
    <Flex direction="column" width="260px" flexShrink="0" style={{ borderRight: "1px solid var(--gray-a5)" }}>
      <Flex style={{ borderBottom: "1px solid var(--gray-a5)" }}>
        <TabButton active={tab === "explore"} onClick={() => setTab("explore")}>Explore</TabButton>
        <TabButton active={tab === "operations"} onClick={() => setTab("operations")}>Operations</TabButton>
        <Flex align="center" pr="1"><IconButton size="1" variant="ghost" color="gray" aria-label="Hide left panel" onClick={onCollapse}><ChevronLeft size={14} /></IconButton></Flex>
      </Flex>
      <Box p="2"><TextField.Root size="1" placeholder={tab === "explore" ? "Find a method" : "Find an operation"} value={query} onChange={(event) => setQuery(event.target.value)}><TextField.Slot><Search size={13} /></TextField.Slot></TextField.Root></Box>
      <ScrollArea style={{ flex: 1 }}>
        <Flex direction="column" gap="1" p="2">
          {tree.map((item, index) => <TreeRow key={`${item.kind}:${item.name}:${index}`} item={item} depth={0} selectedId={selectedId} leafKind={tab === "explore" ? "method" : "operation"} onSelect={select} />)}
        </Flex>
      </ScrollArea>
    </Flex>
  );
}
