from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

EdgeType = Literal["sequence", "invoke", "data"]

# TODO: confirm the returnFrom and fallback fields are correctly updated

@dataclass(slots=True)
class Edge:
    source: str
    target: str
    type: EdgeType

    # flatten_intermethod_cfg only, "sequence" edges synthesized as a
    # method return: the call-site node this return is attributed to (the
    # ORIGINAL call site, not the callee's tail node.
    returnFrom: str | None = None

    # flatten_intermethod_cfg only: True when this return edge is an
    # inferred fallback -- the callee's whole inlined subtree never
    # reached the continuation it was given, so the callee's entry is
    # wired directly to it instead.
    fallback: bool = False

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Edge:
        return cls(
            source=data["from"],
            target=data["to"],
            type=data["type"],
            returnFrom=data.get("returnFrom"),
            fallback=data.get("fallback", False),
        )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"from": self.source, "to": self.target, "type": self.type}
        if self.returnFrom is not None:
            result["returnFrom"] = self.returnFrom
        if self.fallback:
            result["fallback"] = True
        return result
