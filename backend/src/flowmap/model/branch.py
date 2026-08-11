"""
Branch-group manifest: one entry per IF/TRY control structure found during
extraction (full_cfg.sc's emitBranchGroup). Kept as a graph-level list,
separate from Node/Edge -- an arm can be "empty" (zero calls in it,
DESIGN.md #4.1) and has no node of its own to carry this on, so the arm's
existence lives here instead. A non-empty arm's `firstCallId` is a
cross-reference to a Node carrying the matching `branchGroupId`/`armLabel`
(see node.py) -- EVERY call in that arm carries the same tag, not just the
first; `firstCallId` here is only which one the panel should anchor its
initial highlight on.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class BranchArm:
    label: str

    # None when `empty` is True -- an empty arm has no call node of its
    # own to point to.
    firstCallId: str | None = None

    # No further detail is recorded for an empty arm (no throw/return/
    # fallthrough/continues breakdown) -- deliberately dropped after
    # discussion, since the panel only needs to know there's nothing
    # operationally significant in it, not how it technically ends.
    empty: bool = False

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BranchArm:
        return cls(
            label=data["label"],
            firstCallId=data.get("firstCallId"),
            empty=data.get("empty", False),
        )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"label": self.label, "empty": self.empty}
        if self.firstCallId is not None:
            result["firstCallId"] = self.firstCallId
        return result


@dataclass(slots=True)
class BranchGroup:
    # full_cfg.sc's `cs${cs.id}` -- stable across filter/flatten stages,
    # matches the `branchGroupId` tagged on each arm's first-call Node.
    id: str

    # Joern's controlStructureType: "IF" or "TRY" today -- SWITCH/FOR/
    # WHILE/DO aren't split into arms yet (see full_cfg.sc).
    kind: str

    conditionCode: str | None = None
    line: int | None = None
    arms: list[BranchArm] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BranchGroup:
        return cls(
            id=data["id"],
            kind=data["kind"],
            conditionCode=data.get("conditionCode"),
            line=data.get("line"),
            arms=[BranchArm.from_dict(a) for a in data.get("arms", [])],
        )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "id": self.id, "kind": self.kind,
            "arms": [a.to_dict() for a in self.arms],
        }
        if self.conditionCode is not None:
            result["conditionCode"] = self.conditionCode
        if self.line is not None:
            result["line"] = self.line
        return result
