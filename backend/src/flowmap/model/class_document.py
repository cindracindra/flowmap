from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class ClassDocument:
    className: str
    fullName: str
    package: str
    filename: str
    terms: list[str] = field(default_factory=list)
    methodNames: list[str] = field(default_factory=list)
    memberNames: list[str] = field(default_factory=list)
    annotations: list[str] = field(default_factory=list)
    inherits: list[str] = field(default_factory=list)
    identifiers: list[str] = field(default_factory=list)
    comments: list[str] = field(default_factory=list)
    literals: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        # Structured extraction is authoritative. Retain the historical
        # flat terms view for embedding/statistical consumers and for older
        # serialized documents that only contain `terms`.
        if not self.terms:
            self.terms = [
                self.className,
                *self.methodNames,
                *self.memberNames,
                *self.annotations,
                *self.inherits,
                *self.identifiers,
                *self.comments,
                *self.literals,
            ]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ClassDocument:
        return cls(
            className=data["className"],
            fullName=data["fullName"],
            package=data["package"],
            filename=data["filename"],
            terms=list(data.get("terms", [])),
            methodNames=list(data.get("methodNames", [])),
            memberNames=list(data.get("memberNames", [])),
            annotations=list(data.get("annotations", [])),
            inherits=list(data.get("inherits", [])),
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
            "terms": list(self.terms),
            "methodNames": list(self.methodNames),
            "memberNames": list(self.memberNames),
            "annotations": list(self.annotations),
            "inherits": list(self.inherits),
            "identifiers": list(self.identifiers),
            "comments": list(self.comments),
            "literals": list(self.literals),
        }
