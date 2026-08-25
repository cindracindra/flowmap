from __future__ import annotations

from dataclasses import dataclass, field

from .branch import BranchGroup
from .edge import Edge
from .loop import LoopGroup
from .node import Node
from .semantic import NodeSemanticFeatures


@dataclass(slots=True)
class MethodDefinition:
    """Canonical filtered topology for one reusable method definition.

    Every ID is definition-scoped. Phase results and frontend instance state
    deliberately live elsewhere because they are produced at later stages.
    """

    entryId: str
    methodFullName: str
    entry: Node
    # Body nodes only; the entry is represented by ``entry``.
    nodes: list[Node] = field(default_factory=list)
    # Local control flow. Both endpoints belong to this definition.
    sequenceEdges: list[Edge] = field(default_factory=list)
    # Outbound relationships. The source belongs to this definition; the
    # target may be another entry or an external leaf.
    invokeEdges: list[Edge] = field(default_factory=list)
    branchGroups: list[BranchGroup] = field(default_factory=list)
    loopGroups: list[LoopGroup] = field(default_factory=list)
    semanticFeatures: dict[str, NodeSemanticFeatures] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.entry.id != self.entryId or self.entry.type != "entry":
            raise ValueError(
                f"MethodDefinition entry {self.entry.id!r} does not match "
                f"entryId {self.entryId!r}"
            )
        if self.entry.calleeFullName != self.methodFullName:
            raise ValueError(
                f"MethodDefinition {self.entryId!r} full name does not match its entry"
            )
        if any(node.id == self.entryId for node in self.nodes):
            raise ValueError(f"MethodDefinition {self.entryId!r} duplicates its entry")
        if any(edge.type != "sequence" for edge in self.sequenceEdges):
            raise ValueError(f"MethodDefinition {self.entryId!r} has a non-sequence local edge")
        if any(edge.type != "invoke" for edge in self.invokeEdges):
            raise ValueError(f"MethodDefinition {self.entryId!r} has a non-invoke outbound edge")
