from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class ClassDocument:
    className: str
    fullName: str
    package: str
    filename: str
    methodNames: list[str] = field(default_factory=list)
    memberNames: list[str] = field(default_factory=list)
    identifiers: list[str] = field(default_factory=list)
    comments: list[str] = field(default_factory=list)
    literals: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ClassDocument:
        return cls(
            className=data["className"],
            fullName=data["fullName"],
            package=data["package"],
            filename=data["filename"],
            methodNames=list(data.get("methodNames", [])),
            memberNames=list(data.get("memberNames", [])),
            identifiers=list(data.get("identifiers", [])),
            comments=list(data.get("comments", [])),
            literals=list(data.get("literals", [])),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "className": self.className,
            "fullName": self.fullName,
            "package": self.package,
            "filename": self.filename,
            "methodNames": list(self.methodNames),
            "memberNames": list(self.memberNames),
            "identifiers": list(self.identifiers),
            "comments": list(self.comments),
            "literals": list(self.literals),
        }
