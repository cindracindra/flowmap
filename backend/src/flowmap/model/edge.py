from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from .branch import BranchRequirement

EdgeType = Literal["sequence", "invoke", "data"]

@dataclass(slots=True)
class Edge:
    source: str
    target: str
    type: EdgeType

    # Every branch selection that must hold for this method-local edge to
    # execute. This is edge control-flow metadata, not node arm membership.
    branchRequirements: list[BranchRequirement] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Edge:
        return cls(
            source=data["from"],
            target=data["to"],
            type=data["type"],
            branchRequirements=[
                BranchRequirement.from_dict(r)
                for r in data.get("branchRequirements", [])
            ],
        )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"from": self.source, "to": self.target, "type": self.type}
        if self.branchRequirements:
            result["branchRequirements"] = [
                requirement.to_dict() for requirement in self.branchRequirements
            ]
        return result
