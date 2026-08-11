from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class BranchArmRef:
    groupId: str
    armLabel: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BranchArmRef:
        return cls(groupId=data["groupId"], armLabel=data["armLabel"])

    def to_dict(self) -> dict[str, Any]:
        return {"groupId": self.groupId, "armLabel": self.armLabel}


@dataclass(slots=True)
class BranchArm:
    label: str

    # None when `empty` -- arm has no call node of its own to point to.
    firstCallId: str | None = None

    # No further detail is recorded for an empty arm (no throw/return/
    # fallthrough/continues breakdown).
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
    id: str

    # Joern's controlStructureType.
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
