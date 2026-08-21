"""Print one method's scopes, gates and segments.

The analysis is several derived layers deep, so reading it in JSON is painful.

    poetry run python backend/src/flowmap/tools/inspect_phases.py newOrder
    poetry run python backend/src/flowmap/tools/inspect_phases.py newOrder --depth 2
    poetry run python backend/src/flowmap/tools/inspect_phases.py --list
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

FLOWMAP_SRC = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(FLOWMAP_SRC))

from domain.cfg_slicing import filter_and_classify_roots_and_orphans  # noqa: E402
from domain.phase_exclusion import find_excluded_operations  # noqa: E402
from domain.phase_segmentation import (  # noqa: E402
    Analysis,
    RetainedCall,
    Segment,
    analyse,
    callee_count,
    materialise,
    phase_count,
)
from model import Graph  # noqa: E402

PROJECT_ROOT = FLOWMAP_SRC.parents[2]


def load_graph(path: Path | None) -> Graph:
    if path is None:
        config = json.loads((PROJECT_ROOT / "flowmap.config.json").read_text())
        path = (PROJECT_ROOT / config["outputDir"]).resolve() / "full_cfg.json"
    graph = Graph.from_dict(json.loads(path.read_text()))
    return filter_and_classify_roots_and_orphans(graph)


def short(name: str | None) -> str:
    return name.split(":", 1)[0].rsplit(".", 1)[-1] if name else "?"


def code_of(analysis: Analysis, node_id: str, width: int = 46) -> str:
    node = next((n for n in analysis.graph.nodes if n.id == node_id), None)
    text = str(node.code) if node and node.code else short(node.calleeFullName if node else None)
    return text if len(text) <= width else text[: width - 1] + "…"


def entries_matching(analysis: Analysis, needle: str) -> list[str]:
    return [
        node.id
        for node in analysis.graph.nodes
        if node.type == "entry"
        and node.calleeFullName
        and needle in node.calleeFullName
        and node.id in analysis.segments
    ]


def print_scopes(analysis: Analysis, entry_id: str) -> None:
    print("  SCOPES")
    for scope in analysis.scopes.get(entry_id, ()):
        tags = "TOP" if not scope.tags else " + ".join(
            f"{group.split('cs')[-1][-4:]}:{arm}" for group, arm in sorted(scope.tags)
        )
        head = code_of(analysis, scope.nodeIds[0], 34)
        print(f"    [{len(scope.tags)}] {tags:22} {len(scope.nodeIds):>2} ops   {head}")


def print_walk(analysis: Analysis, entry_id: str, depth: int, indent: str = "    ") -> None:
    method = analysis.segments[entry_id]
    gate_before = {gate.candidateId: gate for gate in method.gates}
    boundaries = {
        item.nodeIds[0] if isinstance(item, Segment) else item.callSiteId
        for item in materialise(analysis, entry_id)
    }

    phase = 0
    for node_id in method.sequence:
        gate = gate_before.get(node_id)
        starts_phase = node_id in boundaries
        retained = callee_count(analysis, node_id) > 1

        if gate is not None:
            evidence = []
            if gate.local is not None:
                evidence += list(gate.local.evidence)
            if gate.cohesion is not None:
                evidence += list(gate.cohesion.evidence)
            if gate.override is not None:
                evidence += list(gate.override.evidence)
            mark = "  ──" if starts_phase else "  ··"
            detail = f"{gate.action}/{gate.decidedBy}"
            if gate.kind != "adjacency":
                detail = f"{gate.kind} {detail}"
            print(f"{indent}{mark} {detail:44} {', '.join(evidence[:4])}")

        if starts_phase:
            phase += 1
        label = f"p{phase}" if starts_phase else "  "
        if retained:
            callee = analysis.calleeEntries.get(node_id, ())
            inner = max((phase_count(analysis, e) for e in callee), default=0)
            print(f"{indent} {label} {node_id:22} {code_of(analysis, node_id)}   ⟨retained, {inner} phases⟩")
            if depth > 0:
                for callee_entry in callee:
                    if callee_entry in analysis.segments:
                        name = short(next(
                            n.calleeFullName for n in analysis.graph.nodes if n.id == callee_entry
                        ))
                        print(f"{indent}     └─ {name}")
                        print_walk(analysis, callee_entry, depth - 1, indent + "        ")
        else:
            print(f"{indent} {label} {node_id:22} {code_of(analysis, node_id)}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("method", nargs="?", default="newOrder",
                        help="substring of the method's full name")
    parser.add_argument("--graph", type=Path, default=None)
    parser.add_argument("--depth", type=int, default=1,
                        help="levels of retained callees to expand")
    parser.add_argument("--list", action="store_true", help="list segmented methods and exit")
    args = parser.parse_args()

    graph = load_graph(args.graph)
    analysis = analyse(graph, find_excluded_operations(graph))

    if args.list:
        rows = sorted(
            (
                (phase_count(analysis, entry_id), len(segments.sequence),
                 next(n.calleeFullName for n in graph.nodes if n.id == entry_id))
                for entry_id, segments in analysis.segments.items()
                if segments.sequence
            ),
            reverse=True,
        )
        print(f"{'phases':>7}{'ops':>6}  method")
        for phases, ops, name in rows:
            print(f"{phases:>7}{ops:>6}  {name.split(':', 1)[0]}")
        return

    matches = entries_matching(analysis, args.method)
    if not matches:
        print(f"no segmented method matching {args.method!r}; try --list")
        return

    for entry_id in matches:
        name = next(n.calleeFullName for n in graph.nodes if n.id == entry_id)
        segments = analysis.segments[entry_id]
        items = materialise(analysis, entry_id)
        own = sum(1 for item in items if isinstance(item, Segment))
        held = sum(1 for item in items if isinstance(item, RetainedCall))
        print()
        print("=" * 100)
        print(f"METHOD  {name}")
        print(f"        {len(segments.sequence)} operations · "
              f"{len(analysis.scopes.get(entry_id, ()))} scopes · "
              f"{own} own segments + {held} retained · "
              f"{phase_count(analysis, entry_id)} phases in total")
        print("=" * 100)
        print_scopes(analysis, entry_id)
        print()
        print("  WALK   ── starts a phase   ·· continues one")
        print_walk(analysis, entry_id, args.depth)


if __name__ == "__main__":
    main()
