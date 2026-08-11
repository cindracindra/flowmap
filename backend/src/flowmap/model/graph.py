"""
Graph shape shared across every stage of the CFG pipeline: the raw
extraction (inter_cfg.sc / processor.extract_intermethod_cfg), the noise-
filtered pass (processor.filter_intermethod_cfg), and the flattened trace
(processor.flatten_intermethod_cfg). Which optional fields are populated
depends on the stage -- see each field's comment.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .branch import BranchGroup
from .edge import Edge
from .node import Node


@dataclass(slots=True)
class Graph:
    # Full name of the method this graph is rooted at. None for a whole-
    # codebase graph (extract_full_intermethod_cfg) -- there's no single
    # root; see `roots` below instead.
    entryPoint: str | None = None

    nodes: list[Node] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)

    # flatten_intermethod_cfg only: the cloned id of the entry node the
    # flattened trace is rooted at. On the raw/filtered graph there are no
    # clones, so the entry node is found by matching `entryPoint` against
    # nodes instead (see e.g. visualizer.py's _find_entry_node_id).
    rootId: str | None = None

    # classify_roots_and_orphans only (whole-codebase graph): ids of
    # "entry" nodes with no caller but at least one callee -- candidate
    # application entry points. Not populated by any single-entry-point
    # stage, where the graph's own entryPoint already answers this.
    roots: list[str] = field(default_factory=list)

    # classify_roots_and_orphans only: ids of "entry" nodes with neither a
    # caller nor a callee -- unreachable from anything this pass can see,
    # and reaching nothing itself.
    orphans: list[str] = field(default_factory=list)

    # Extraction only (full_cfg.sc's emitBranchGroup): one entry per
    # IF/TRY control structure encountered across every method in this
    # graph. Method-level metadata, not per-node -- carried through
    # filter_noise_cfg/flatten_cfg unchanged (a branch group's shape
    # doesn't change when nodes are filtered/cloned; only the nodes whose
    # branchGroupId points at it do). See branch.py.
    branchGroups: list[BranchGroup] = field(default_factory=list)

    @property
    def deadEndIds(self) -> list[str]:
        """Derived from each node's own `deadEnd` flag, not stored
        separately -- a node is the only source of truth for its own
        dead-end status (see Node.deadEnd)."""
        return [n.id for n in self.nodes if n.deadEnd]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Graph:
        return cls(
            entryPoint=data.get("entryPoint"),
            nodes=[Node.from_dict(n) for n in data.get("nodes", [])],
            edges=[Edge.from_dict(e) for e in data.get("edges", [])],
            rootId=data.get("rootId"),
            roots=list(data.get("roots", [])),
            orphans=list(data.get("orphans", [])),
            branchGroups=[BranchGroup.from_dict(g) for g in data.get("branchGroups", [])],
        )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "nodes": [n.to_dict() for n in self.nodes],
            "edges": [e.to_dict() for e in self.edges],
        }
        if self.entryPoint is not None:
            result["entryPoint"] = self.entryPoint
        if self.rootId is not None:
            result["rootId"] = self.rootId
        if self.roots:
            result["roots"] = self.roots
        if self.orphans:
            result["orphans"] = self.orphans
        if self.branchGroups:
            result["branchGroups"] = [g.to_dict() for g in self.branchGroups]
        return result
