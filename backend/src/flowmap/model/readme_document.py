from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class ReadmeDocument:
    path: str
    package: str
    text: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ReadmeDocument:
        return cls(path=data["path"], package=data["package"], text=data["text"])

    def to_dict(self) -> dict[str, Any]:
        return {"path": self.path, "package": self.package, "text": self.text}
