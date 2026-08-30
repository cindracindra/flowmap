from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


ExitKind = Literal["return", "throw", "break", "continue", "continues"]


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


@dataclass(frozen=True, slots=True)
class ConditionStage:
    """One canonical condition evaluation in a flattened IF/else-if chain."""

    id: str
    nodeIds: list[str] = field(default_factory=list)
    decisionNodeId: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ConditionStage:
        return cls(
            id=data["id"],
            nodeIds=list(data.get("nodeIds", [])),
            decisionNodeId=data.get("decisionNodeId"),
        )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"id": self.id, "nodeIds": self.nodeIds}
        if self.decisionNodeId is not None:
            result["decisionNodeId"] = self.decisionNodeId
        return result


@dataclass(frozen=True, slots=True)
class ArmConditionStage:
    """Outcome required at one condition stage to select a final arm."""

    stageId: str
    nodeIds: list[str] = field(default_factory=list)
    outcome: bool = False

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ArmConditionStage:
        return cls(
            stageId=data["stageId"],
            nodeIds=list(data.get("nodeIds", [])),
            outcome=data["outcome"],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "stageId": self.stageId,
            "nodeIds": self.nodeIds,
            "outcome": self.outcome,
        }


@dataclass(slots=True)
class ArmExit:
    """One path-level outcome from a branch arm.

    Physical edges own routing.  This value only identifies the outcome and
    its structural/terminal destination for presentation metadata.
    """

    kind: ExitKind
    destinationNodeId: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ArmExit:
        return cls(
            kind=data["kind"],
            destinationNodeId=data.get("destinationNodeId"),
        )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"kind": self.kind}
        if self.destinationNodeId is not None:
            result["destinationNodeId"] = self.destinationNodeId
        return result


@dataclass(slots=True)
class BranchArm:
    label: str

    # No surviving call in this arm.
    empty: bool = False

    # Path-level authoritative outcomes; every retained arm has at least one.
    exits: list[ArmExit] = field(default_factory=list)

    # The `if`/`else if` condition that selects this arm. Absent on an
    # `else` arm (no condition of its own) and on every TRY arm.
    conditionCode: str | None = None

    # Flattened IF/else-if selection path. Each item references one canonical
    # group-level ConditionStage; condition calls themselves remain unique.
    conditionStages: list[ArmConditionStage] = field(default_factory=list)

    # TRY catch arms only: the declared caught exception type. Kept
    # separate from conditionCode because a catch type is dispatch metadata,
    # not a boolean expression.
    exceptionType: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BranchArm:
        return cls(
            label=data["label"],
            empty=data.get("empty", False),
            exits=[ArmExit.from_dict(exit_) for exit_ in data["exits"]],
            conditionCode=data.get("conditionCode"),
            conditionStages=[
                ArmConditionStage.from_dict(stage)
                for stage in data.get("conditionStages", [])
            ],
            exceptionType=data.get("exceptionType"),
        )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"label": self.label, "empty": self.empty}
        result["exits"] = [exit_.to_dict() for exit_ in self.exits]
        if self.conditionCode is not None:
            result["conditionCode"] = self.conditionCode
        if self.conditionStages:
            result["conditionStages"] = [
                stage.to_dict() for stage in self.conditionStages
            ]
        if self.exceptionType is not None:
            result["exceptionType"] = self.exceptionType
        return result


@dataclass(slots=True)
class BranchGroup:
    id: str

    # Joern's controlStructureType.
    kind: str

    # Authoritative structural boundary before condition/dispatch evaluation.
    entryNodeId: str

    # Owning method's full name
    method: str | None = None

    line: int | None = None
    arms: list[BranchArm] = field(default_factory=list)

    # Absent only when every arm is terminal.
    exitNodeId: str | None = None

    # Canonical execution stages for a flattened IF/else-if chain.
    conditionStages: list[ConditionStage] = field(default_factory=list)

    # Lexical selections enclosing this structure, copied from its entry
    # anchor. This is never reconstructed from incoming edges.
    enclosingRequirements: list[BranchRequirement] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BranchGroup:
        return cls(
            id=data["id"],
            kind=data["kind"],
            entryNodeId=data["entryNodeId"],
            method=data.get("method"),
            line=data.get("line"),
            arms=[BranchArm.from_dict(a) for a in data.get("arms", [])],
            exitNodeId=data.get("exitNodeId"),
            conditionStages=[
                ConditionStage.from_dict(stage)
                for stage in data.get("conditionStages", [])
            ],
            enclosingRequirements=[
                BranchRequirement.from_dict(requirement)
                for requirement in data.get("enclosingRequirements", [])
            ],
        )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"id": self.id, "kind": self.kind}
        if self.method is not None:
            result["method"] = self.method
        if self.line is not None:
            result["line"] = self.line
        result["entryNodeId"] = self.entryNodeId
        if self.exitNodeId is not None:
            result["exitNodeId"] = self.exitNodeId
        if self.conditionStages:
            result["conditionStages"] = [
                stage.to_dict() for stage in self.conditionStages
            ]
        if self.enclosingRequirements:
            result["enclosingRequirements"] = [
                requirement.to_dict()
                for requirement in self.enclosingRequirements
            ]
        result["arms"] = [a.to_dict() for a in self.arms]
        return result
