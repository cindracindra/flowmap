from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class MethodDocument:
    fullName: str
    terms: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MethodDocument:
        return cls(fullName=data["fullName"], terms=list(data.get("terms", [])))

    def to_dict(self) -> dict[str, Any]:
        return {"fullName": self.fullName, "terms": list(self.terms)}
