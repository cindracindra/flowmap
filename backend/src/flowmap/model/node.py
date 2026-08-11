"""
Node shape shared across every stage of the CFG pipeline: the raw
extraction (inter_cfg.sc / processor.extract_intermethod_cfg), the noise-
filtered pass (processor.filter_intermethod_cfg), and the flattened trace
(processor.flatten_intermethod_cfg).

One dataclass is used for all three `type` values ("entry"/"call"/"leaf")
and all three stages, rather than a subclass per type: the pipeline itself
branches on `type` at read time (see e.g. processor.py's `node["type"] ==
"call"` checks), so a matching Python type hierarchy would just add a
parallel structure without removing that branching. Which fields are
actually populated depends on both `type` and pipeline stage -- see each
field's comment.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

NodeType = Literal["entry", "call", "leaf"]

# Matches full_cfg.sc's classifyTerminus. "throw" is the only value that's
# a proven non-return -- see Node.terminus and Node.deadEnd below.
Terminus = Literal["throw", "return", "fallthrough", "continues"]


@dataclass(slots=True)
class Node:
    id: str
    type: NodeType

    # entry: the method this node represents.
    # call: the method this call site invokes.
    # leaf (external touchpoint, i.e. `reason` is absent): same.
    calleeFullName: str | None = None

    # call only: the method this call site lives inside.
    callerMethod: str | None = None

    # call only: source text of the call expression.
    code: str | None = None

    # entry, call: source line number (-1 if Joern couldn't resolve one).
    line: int | None = None

    # leaf only, when calleeFullName is absent: why no callee was
    # resolved (currently always "unresolved" -- see inter_cfg.sc).
    reason: str | None = None

    # flatten_intermethod_cfg only: the pre-clone id this node was copied
    # from (flattening clones a method's nodes fresh per call site, so the
    # same original node can produce many flattened Nodes).
    origId: str | None = None

    # filter_intermethod_cfg only: True iff `terminus == "throw"` for this
    # node -- see `terminus` below for the ground-truth source. None on
    # the raw extraction stage, where the concept doesn't apply yet.
    # Carried forward automatically when flatten_intermethod_cfg clones a
    # node (clones inherit every field of the original), so a clone is
    # correctly a dead end whenever its original was, with no separate
    # tracking needed. See PHASING_RULES.md R3/R4 for how phaser.py uses
    # this.
    deadEnd: bool | None = None

    # call only, extraction stage (full_cfg.sc's classifyTerminus): set
    # only when this call's own forward cfgNext walk found no further
    # call. "throw" is the only value that's a proven non-return -- it's
    # the sole source of `deadEnd` above. "return" (an explicit `return`
    # statement) and "fallthrough" (the method's own implicit end, no
    # return keyword) both mean normal completion and are wired to a
    # caller's pending continuation the same as any node with a real
    # successor -- see cfg_pipeline.py's `inline()`. Absent/None means
    # this call had a real successor and isn't a terminus at all.
    # ("continues" is a defensive value here, not an expected one -- see
    # full_cfg.sc's classifyTerminus docstring for why a call whose own
    # nextCalls came back empty can't actually walk forward into another
    # call afterward.)
    terminus: Terminus | None = None

    # call only, extraction stage (full_cfg.sc's emitBranchGroup): set on
    # EVERY call inside a branch arm (an IF/TRY control structure's
    # then/else/try/catch/finally), not just its first, identifying which
    # BranchGroup (see branch.py) and which of its arms this call belongs
    # to. None for a call that isn't part of any branch arm.
    branchGroupId: str | None = None
    armLabel: str | None = None

    # flatten_intermethod_cfg only: 0-1 BFS depth (sequence edges cost 0,
    # invoke edges cost 1) computed against the PRE-flatten filtered
    # graph, looked up here by origId at clone time. NOT safe to
    # recompute fresh on the flattened graph's own edges -- a
    # fallback/returnFrom-tagged "sequence" edge has no edge-type-based
    # cost that gives the right answer for what crossing it should mean
    # for depth (DESIGN.md's "Sixth bug"/"Seventh bug", §8, and the
    # 2026-08-10 session-log entry, §0).
    depth: int | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Node:
        return cls(
            id=data["id"],
            type=data["type"],
            calleeFullName=data.get("calleeFullName"),
            callerMethod=data.get("callerMethod"),
            code=data.get("code"),
            line=data.get("line"),
            reason=data.get("reason"),
            origId=data.get("origId"),
            deadEnd=data.get("deadEnd"),
            terminus=data.get("terminus"),
            branchGroupId=data.get("branchGroupId"),
            armLabel=data.get("armLabel"),
            depth=data.get("depth"),
        )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"id": self.id, "type": self.type}
        for name, value in (
            ("calleeFullName", self.calleeFullName),
            ("callerMethod", self.callerMethod),
            ("code", self.code),
            ("line", self.line),
            ("reason", self.reason),
            ("origId", self.origId),
            ("terminus", self.terminus),
            ("branchGroupId", self.branchGroupId),
            ("armLabel", self.armLabel),
            ("depth", self.depth),
        ):
            if value is not None:
                result[name] = value
        if self.deadEnd:
            result["deadEnd"] = True
        return result
