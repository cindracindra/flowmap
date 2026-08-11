from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .branch import BranchGroup
from .edge import Edge
from .node import Node


@dataclass(slots=True)
class Graph:
    # Full name of the method this graph is rooted at.
    entryPoint: str | None = None

    nodes: list[Node] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)

    # flatten_intermethod_cfg only: the cloned id of the entry node the
    # flattened trace is rooted at. On the raw/filtered graph there are no
    # clones, so the entry node is found by matching `entryPoint` against
    # nodes instead (see e.g. visualizer.py's _find_entry_node_id).
    rootId: str | None = None

    # classify_roots_and_orphans only (whole-codebase graph): ids of
    # "entry" nodes with no caller but at least one callee.
    roots: list[str] = field(default_factory=list)

    # classify_roots_and_orphans only: ids of "entry" nodes with neither a
    # caller nor a callee.
    orphans: list[str] = field(default_factory=list)

    branchGroups: list[BranchGroup] = field(default_factory=list)

    @property
    def deadEndIds(self) -> list[str]:
        """Derived from each node's own `deadEnd` flag, not stored
        separately (see Node.deadEnd)."""
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
