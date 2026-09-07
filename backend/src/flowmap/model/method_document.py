from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class MethodDocument:
    methodName: str
    fullName: str
    identifiers: list[str] = field(default_factory=list)
    comments: list[str] = field(default_factory=list)
    literals: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MethodDocument:
        return cls(
            methodName=data["methodName"],
            fullName=data["fullName"],
            identifiers=list(data.get("identifiers", [])),
            comments=list(data.get("comments", [])),
            literals=list(data.get("literals", [])),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "methodName": self.methodName,
            "fullName": self.fullName,
            "identifiers": list(self.identifiers),
            "comments": list(self.comments),
            "literals": list(self.literals),
        }
