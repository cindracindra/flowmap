from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from .branch import BranchArmRef

NodeType = Literal["entry", "call", "structure", "transfer", "leaf", "exit"]

MethodExitKind = Literal["return", "throw", "fallthrough"]
TransferKind = Literal["break", "continue"]

StructureRole = Literal["entry", "exit", "decision"]

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

    # structure only: stable ownership and role for explicit control-flow
    # anchors.
    structureGroupId: str | None = None
    structureRole: StructureRole | None = None

    # Structural transfer only: structure whose exit anchor receives it.
    # Kept separate from lexical loopIds because labeled transfers may target
    # an outer structure rather than the nearest enclosing one.
    targetStructureGroupId: str | None = None
    # type="transfer" only. Kept distinct from method exitKind so
    # control transfer is never mistaken for method termination.
    transferKind: TransferKind | None = None

    # entry, call, branch: source line number (-1 if unresolved).
    line: int | None = None

    # entry, call: source file containing the method/call.
    sourceFile: str | None = None

    # entry only: True for Joern auto-generated default constructor.
    implicitConstructor: bool | None = None

    # leaf only, when calleeFullName is absent: why no callee was
    # resolved (currently always "unresolved" -- see inter_cfg.sc).
    reason: str | None = None

    # True when extraction proves this call is on a terminal throw route.
    deadEnd: bool | None = None

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

    def __post_init__(self) -> None:
        if self.type not in {"entry", "call", "structure", "transfer", "leaf", "exit"}:
            raise ValueError(f"Unsupported node type {self.type!r}")
        if self.structureRole not in {None, "entry", "exit", "decision"}:
            raise ValueError(f"Unsupported structure role {self.structureRole!r}")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Node:
        return cls(
            id=data["id"],
            type=data["type"],
            calleeFullName=data.get("calleeFullName"),
            callerMethod=data.get("callerMethod"),
            code=data.get("code"),
            structureGroupId=data.get("structureGroupId"),
            structureRole=data.get("structureRole"),
            targetStructureGroupId=data.get("targetStructureGroupId"),
            transferKind=data.get("transferKind"),
            line=data.get("line"),
            sourceFile=data.get("sourceFile"),
            implicitConstructor=data.get("implicitConstructor"),
            reason=data.get("reason"),
            deadEnd=data.get("deadEnd"),
            exitKind=data.get("exitKind"),
            branchArms=[BranchArmRef.from_dict(t) for t in data.get("branchArms", [])],
            loopIds=list(data.get("loopIds", [])),
        )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"id": self.id, "type": self.type}
        for name, value in (
            ("calleeFullName", self.calleeFullName),
            ("callerMethod", self.callerMethod),
            ("code", self.code),
            ("structureGroupId", self.structureGroupId),
            ("structureRole", self.structureRole),
            ("targetStructureGroupId", self.targetStructureGroupId),
            ("transferKind", self.transferKind),
            ("line", self.line),
            ("sourceFile", self.sourceFile),
            ("implicitConstructor", self.implicitConstructor),
            ("reason", self.reason),
            ("exitKind", self.exitKind),
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
