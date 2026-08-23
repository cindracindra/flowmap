from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from .branch import BranchRequirement

EdgeType = Literal["sequence", "invoke", "data"]

# TODO: confirm the returnFrom and fallback fields are correctly updated

@dataclass(slots=True)
class Edge:
    source: str
    target: str
    type: EdgeType

    # flatten_intermethod_cfg only, "sequence" edges synthesized as a
    # method return: the call-site node this return is attributed to the
    # ORIGINAL call site, not the callee's tail node.
    returnFrom: str | None = None

    # flatten_intermethod_cfg only: True when this return edge is an
    # inferred fallback -- the callee's whole inlined subtree never
    # reached the continuation it was given, so the callee's entry is
    # wired directly to it instead.
    fallback: bool = False

    # Flatten stage: a sequence edge that repeats a source-level loop.
    # Kept as semantic metadata, but excluded from the linear visual route.
    loopBack: bool = False

    # flatten_cfg only: every branch selection that must hold for this
    # edge to execute. This is edge control-flow metadata, not node arm
    # membership: most importantly, a filtered zero-call normal arm owns
    # the synthesized fallback return edge that represents it.
    branchRequirements: list[BranchRequirement] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Edge:
        return cls(
            source=data["from"],
            target=data["to"],
            type=data["type"],
            returnFrom=data.get("returnFrom"),
            fallback=data.get("fallback", False),
            loopBack=data.get("loopBack", False),
            branchRequirements=[
                BranchRequirement.from_dict(r)
                for r in data.get("branchRequirements", [])
            ],
        )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"from": self.source, "to": self.target, "type": self.type}
        if self.returnFrom is not None:
            result["returnFrom"] = self.returnFrom
        if self.fallback:
            result["fallback"] = True
        if self.loopBack:
            result["loopBack"] = True
        if self.branchRequirements:
            result["branchRequirements"] = [
                requirement.to_dict() for requirement in self.branchRequirements
            ]
        return result
