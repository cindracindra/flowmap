from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


LoopKind = Literal["FOR", "FOR_EACH", "WHILE", "DO", "DO_WHILE"]


@dataclass(slots=True)
class LoopGroup:
    """Source-level loop metadata retained beside the call-projected CFG."""

    id: str
    kind: LoopKind
    entryNodeId: str
    exitNodeId: str
    method: str | None = None
    line: int | None = None
    conditionCode: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LoopGroup:
        return cls(
            id=data["id"],
            kind=data["kind"],
            entryNodeId=data["entryNodeId"],
            exitNodeId=data["exitNodeId"],
            method=data.get("method"),
            line=data.get("line"),
            conditionCode=data.get("conditionCode"),
        )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"id": self.id, "kind": self.kind}
        if self.method is not None:
            result["method"] = self.method
        if self.line is not None:
            result["line"] = self.line
        if self.conditionCode is not None:
            result["conditionCode"] = self.conditionCode
        result["entryNodeId"] = self.entryNodeId
        result["exitNodeId"] = self.exitNodeId
        return result
