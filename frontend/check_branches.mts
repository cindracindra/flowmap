// Temporary harness: exercises lib/branches.ts against the real fixture.
// Not part of the app; delete after inspecting.
import graphRaw from "./src/data/flattened_cfg.json";
import type { FlowGraph } from "./src/types/flowmap";
import { buildBranchPanels, defaultSelection, visibleNodeIds } from "./src/lib/branches";

const graph = graphRaw as unknown as FlowGraph;
const panels = buildBranchPanels(graph, graph.rootId!);
const label = (id?: string) => {
  if (!id) return "(none)";
  const n = graph.nodes.find((x) => x.id === id);
  return (n?.code ?? n?.calleeFullName ?? id).slice(0, 44);
};

const cond = panels.filter((p) => p.kind === "conditional").length;
console.log(`panels: ${panels.length} (conditional ${cond}, polymorphic ${panels.length - cond})`);
console.log(`groups in data: ${graph.branchGroups!.length} -> dropped ${graph.branchGroups!.length - cond} with all-empty arms\n`);

for (const p of panels) {
  console.log(`${p.kind === "polymorphic" ? "D" : "C"} ${p.id} [${p.structure}] line=${p.line} switcher=${p.switcherPosition} default=${p.defaultArmId ?? "(none)"}`);
  console.log(`    "${p.title}" / "${p.subtitle ?? ""}"`);
  console.log(`    anchor=${label(p.branchPointIds[0])}  converges=${label(p.convergesAt)}`);
  for (const a of p.arms) {
    console.log(`      ${a.role.padEnd(11)} ${a.id.padEnd(20)} "${a.label}" empty=${String(a.empty).padEnd(5)} exit=${a.exitKind}->${label(a.exitTargetId)} region=${a.memberIds.length}`);
  }
}

let overlaps = 0;
for (const p of panels) {
  const seen = new Map<string, string>();
  for (const a of p.arms) {
    for (const id of a.memberIds) {
      const prev = seen.get(id);
      if (prev && prev !== a.id) {
        overlaps++;
        console.log(`  OVERLAP in ${p.id}: ${id} in both ${prev} and ${a.id}`);
      }
      seen.set(id, a.id);
    }
  }
}
console.log(`\nintra-panel arm overlaps: ${overlaps}`);

const sel = defaultSelection(panels);
console.log(`visible at default selection: ${visibleNodeIds(graph, panels, sel).size}/${graph.nodes.length}`);

const dispatch = panels.find((p) => p.kind === "polymorphic")!;
for (const arm of dispatch.arms) {
  const s = new Map(sel);
  s.set(dispatch.id, arm.id);
  console.log(`  dispatch -> ${arm.label.padEnd(24)} visible ${visibleNodeIds(graph, panels, s).size}`);
}

// Isolate the TRY panel so unrelated panels cannot affect its arm counts.
const tryPanel = panels.find((p) => p.structure === "TRY" && p.arms.length === 3)!;
console.log(`\nTRY panel ${tryPanel.id} (isolated):`);
for (const choice of tryPanel.arms.map((a) => a.id)) {
  const s = new Map([[tryPanel.id, choice]]);
  const v = visibleNodeIds(graph, [tryPanel], s);
  const shown = (armId: string) =>
    tryPanel.arms.find((a) => a.id === armId)!.memberIds.filter((id) => v.has(id)).length;
  console.log(
    `  select ${String(choice).padEnd(8)} visible ${String(v.size).padStart(3)}` +
      `  noCatch ${shown("noCatch")}` +
      `  catch1 ${shown("catch1")}  catch2 ${shown("catch2")}`,
  );
}
