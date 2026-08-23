from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


ArmTerminus = Literal["throw", "return", "continues"]
ExitKind = Literal["return", "throw", "continues"]


@dataclass(frozen=True, slots=True)
class BranchArmRef:
    groupId: str
    armLabel: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BranchArmRef:
        return cls(groupId=data["groupId"], armLabel=data["armLabel"])

    def to_dict(self) -> dict[str, Any]:
        return {"groupId": self.groupId, "armLabel": self.armLabel}


@dataclass(frozen=True, slots=True)
class BranchRequirement:
    """A branch selection required for an edge to be executable.

    The label names a real arm in the corresponding group. TRY normal
    completion uses its explicit empty ``noCatch`` arm.
    """

    groupId: str
    armLabel: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BranchRequirement:
        return cls(groupId=data["groupId"], armLabel=data["armLabel"])

    def to_dict(self) -> dict[str, Any]:
        return {"groupId": self.groupId, "armLabel": self.armLabel}


@dataclass(slots=True)
class ArmExit:
    """One path-level outcome from a branch arm.

    Extraction records original frontier ids. Flattening scopes those ids to
    clones and resolves targetIds. Requirements are route constraints beyond
    the owning arm, notably those introduced by nested branches.
    """

    kind: ExitKind
    frontierIds: list[str] = field(default_factory=list)
    targetIds: list[str] = field(default_factory=list)
    branchRequirements: list[BranchRequirement] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ArmExit:
        return cls(
            kind=data["kind"],
            frontierIds=list(data.get("frontierIds", [])),
            targetIds=list(data.get("targetIds", [])),
            branchRequirements=[
                BranchRequirement.from_dict(requirement)
                for requirement in data.get("branchRequirements", [])
            ],
        )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"kind": self.kind}
        if self.frontierIds:
            result["frontierIds"] = self.frontierIds
        if self.targetIds:
            result["targetIds"] = self.targetIds
        if self.branchRequirements:
            result["branchRequirements"] = [
                requirement.to_dict() for requirement in self.branchRequirements
            ]
        return result


@dataclass(slots=True)
class BranchArm:
    label: str

    # The arm's head: its FIRST call in CFG order. None when `empty`.
    #
    # At extraction this is the arm's first call in AST order, which is not
    # the same thing -- `throw new Foo(bar())` evaluates bar(), then the
    # constructor, then the throw, so the AST-first call is the CFG-LAST
    # one. filter_noise_cfg recomputes it (see _recompute_branch_geometry)
    # against the surviving nodes in real flow order; treat the extraction
    # value as provisional.
    firstCallId: str | None = None

    # No surviving call in this arm.
    empty: bool = False

    # How this arm exits -- set for every arm, empty ones included.
    terminus: ArmTerminus | None = None

    # Path-level authoritative outcomes. Empty on legacy artifacts, where
    # consumers fall back to `terminus` through arm_exit_kinds().
    exits: list[ArmExit] = field(default_factory=list)

    # The `if`/`else if` condition that selects this arm. Absent on an
    # `else` arm (no condition of its own) and on every TRY arm.
    conditionCode: str | None = None

    # TRY catch arms only: the declared caught exception type. Kept
    # separate from conditionCode because a catch type is dispatch metadata,
    # not a boolean expression.
    exceptionType: str | None = None

    # Flatten stage only: visible destinations after this arm exits.
    # None means this stage has not resolved destinations yet. The final
    # flattened graph always carries a list: [] for a throw or for flow
    # leaving the visible trace, caller continuation(s) for return, and the
    # group's normal continuation for continues.
    targetIds: list[str] | None = None

    def __post_init__(self) -> None:
        if self.terminus == "throw" and self.targetIds:
            raise ValueError(
                f"Throwing arm {self.label!r} cannot have normal-flow targets"
            )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BranchArm:
        return cls(
            label=data["label"],
            firstCallId=data.get("firstCallId"),
            empty=data.get("empty", False),
            terminus=data.get("terminus"),
            exits=[ArmExit.from_dict(exit_) for exit_ in data.get("exits", [])],
            conditionCode=data.get("conditionCode"),
            exceptionType=data.get("exceptionType"),
            targetIds=(list(data["targetIds"]) if "targetIds" in data else None),
        )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"label": self.label, "empty": self.empty}
        if self.terminus is not None:
            result["terminus"] = self.terminus
        if self.exits:
            result["exits"] = [exit_.to_dict() for exit_ in self.exits]
        if self.conditionCode is not None:
            result["conditionCode"] = self.conditionCode
        if self.exceptionType is not None:
            result["exceptionType"] = self.exceptionType
        if self.firstCallId is not None:
            result["firstCallId"] = self.firstCallId
        if self.targetIds is not None:
            result["targetIds"] = self.targetIds
        return result


def arm_exit_kinds(arm: BranchArm) -> set[ExitKind]:
    """Authoritative exit kinds with compatibility for old artifacts."""
    if arm.exits:
        return {exit_.kind for exit_ in arm.exits}
    return {arm.terminus or "continues"}


def legacy_terminus(exits: list[ArmExit]) -> ArmTerminus:
    """Conservative compatibility summary for path-level exits."""
    kinds = {exit_.kind for exit_ in exits}
    if kinds == {"throw"}:
        return "throw"
    if kinds == {"return"}:
        return "return"
    return "continues"


@dataclass(slots=True)
class BranchGroup:
    id: str

    # Joern's controlStructureType.
    kind: str

    # Owning method's full name
    method: str | None = None

    line: int | None = None
    arms: list[BranchArm] = field(default_factory=list)

    # filter_noise_cfg: the node(s) the fork hangs off, i.e. where the
    # panel attaches. A group is not a node, so without this it has
    # nowhere to be drawn. Empty when no arm has a surviving head to work
    # back from.
    #
    # A LIST because a TRY has one fork per try tail: an exception can
    # divert to the handler from the end of any path through the try body,
    # so `try { a(); if (x) b(); else c(); } catch ...` forks at BOTH b()
    # and c(). An IF always has exactly one.
    branchPointIds: list[str] = field(default_factory=list)

    # NOT POPULATED YET -- belongs to flatten_cfg, see below.
    #
    # Where the arms rejoin: the "X" in an empty arm's "skip to X". Can't
    # be computed at filter time, where sequence edges don't cross methods:
    # a branch that ends its method converges on the CALLER's next call,
    # which no edge reaches until flattening synthesizes the returnFrom
    # edge. It's also per-call-site -- a method inlined twice converges in
    # two different places -- so it can't be one pre-clone value either.
    convergesAt: str | None = None

    # flatten_cfg internal only: where the enclosing method instance returns to.
    # An arm whose terminus is "return" leaves the method entirely, so its
    # arrow points here, NOT at convergesAt -- with code after the branch
    # the two differ (the normal path runs it, the returning arm skips it).
    # _analyze_branch_routes consumes this into each arm's targetIds; it is
    # deliberately omitted from serialized output so the frontend receives
    # only the resolved route contract.
    returnsTo: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BranchGroup:
        return cls(
            id=data["id"],
            kind=data["kind"],
            method=data.get("method"),
            line=data.get("line"),
            arms=[BranchArm.from_dict(a) for a in data.get("arms", [])],
            branchPointIds=list(data.get("branchPointIds", [])),
            convergesAt=data.get("convergesAt"),
            returnsTo=list(data.get("returnsTo", [])),
        )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"id": self.id, "kind": self.kind}
        if self.method is not None:
            result["method"] = self.method
        if self.line is not None:
            result["line"] = self.line
        if self.branchPointIds:
            result["branchPointIds"] = self.branchPointIds
        if self.convergesAt is not None:
            result["convergesAt"] = self.convergesAt
        result["arms"] = [a.to_dict() for a in self.arms]
        return result
