"""
Edge shape shared across every stage of the CFG pipeline: the raw
extraction (inter_cfg.sc / processor.extract_intermethod_cfg), the noise-
filtered pass (processor.filter_intermethod_cfg), and the flattened trace
(processor.flatten_intermethod_cfg).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

EdgeType = Literal["sequence", "invoke", "data"]


@dataclass(slots=True)
class Edge:
    # Named source/target rather than from/to: "from" is a Python keyword,
    # and the raw pipeline dicts use "from"/"to" as JSON keys (see
    # from_dict/to_dict below for that mapping).
    source: str
    target: str
    type: EdgeType

    # flatten_intermethod_cfg only, "sequence" edges synthesized as a
    # method return: the call-site node this return is attributed to (the
    # ORIGINAL call site, not the callee's tail node -- see
    # flatten_intermethod_cfg's docstring, DESIGN.md #8.4).
    returnFrom: str | None = None

    # flatten_intermethod_cfg only: True when this return edge is an
    # inferred fallback -- the callee's whole inlined subtree never
    # reached the continuation it was given, so the callee's entry is
    # wired directly to it instead (see flatten_intermethod_cfg's
    # "fallback edge" branch).
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
