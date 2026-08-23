from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from .branch import BranchArmRef

NodeType = Literal["entry", "call", "leaf", "exit"]

MethodExitKind = Literal["return", "throw", "fallthrough"]

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

    # entry, call: source file containing the method/call. 
    sourceFile: str | None = None

    # entry only: True for Joern auto-generated default constructor.
    implicitConstructor: bool | None = None

    # leaf only, when calleeFullName is absent: why no callee was
    # resolved (currently always "unresolved" -- see inter_cfg.sc).
    reason: str | None = None

    # flatten_intermethod_cfg only: pre-clone id this node was copied from.
    origId: str | None = None

    # filter_intermethod_cfg only: True iff `terminus == "throw"` for this
    # node.
    deadEnd: bool | None = None

    # call only: set only when this call's own forward cfgNext walk found 
    # no further call - "throw" (non-return), "return" (an explicit `return`
    # statement) and "fallthrough" (the method's own implicit end, no
    # return keyword). Absent/None when call had a real successor and isn't 
    # a terminus at all.
    terminus: Terminus | None = None

    # extraction only: authoritative method-local control-flow exit. Explicit
    # RETURN and throw exits retain their source construct; fallthrough is the
    # method's implicit METHOD_RETURN. Flattening consumes these nodes rather
    # than exposing them as operations.
    exitKind: MethodExitKind | None = None

    # call only: every (group, arm) this call is a member of. Empty for a 
    # call that isn't part of any branch arm.
    branchArms: list[BranchArmRef] = field(default_factory=list)

    # Source loops whose body contains this node.
    loopIds: list[str] = field(default_factory=list)

    # TODO: to be removed
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
            sourceFile=data.get("sourceFile"),
            implicitConstructor=data.get("implicitConstructor"),
            reason=data.get("reason"),
            origId=data.get("origId"),
            deadEnd=data.get("deadEnd"),
            terminus=data.get("terminus"),
            exitKind=data.get("exitKind"),
            branchArms=[BranchArmRef.from_dict(t) for t in data.get("branchArms", [])],
            loopIds=list(data.get("loopIds", [])),
            depth=data.get("depth"),
        )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"id": self.id, "type": self.type}
        for name, value in (
            ("calleeFullName", self.calleeFullName),
            ("callerMethod", self.callerMethod),
            ("code", self.code),
            ("line", self.line),
            ("sourceFile", self.sourceFile),
            ("implicitConstructor", self.implicitConstructor),
            ("reason", self.reason),
            ("origId", self.origId),
            ("terminus", self.terminus),
            ("exitKind", self.exitKind),
            ("depth", self.depth),
        ):
            if value is not None:
                result[name] = value
        if self.branchArms:
            result["branchArms"] = [t.to_dict() for t in self.branchArms]
        if self.loopIds:
            result["loopIds"] = self.loopIds
        if self.deadEnd:
            result["deadEnd"] = True
        return result
