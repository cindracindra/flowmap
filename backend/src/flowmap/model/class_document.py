"""
Output shape of service.topic.extract_class_documents -- one entry per
project class, its raw term bag straight from class_document.sc (method/
member/annotation/inherited-type/identifier/comment/string-literal names),
pre-preprocessing. Consumed by domain.topic_modelling's embedding/clustering pipeline.
"""

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

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ClassDocument:
        return cls(
            className=data["className"],
            fullName=data["fullName"],
            package=data["package"],
            filename=data["filename"],
            terms=list(data.get("terms", [])),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "className": self.className,
            "fullName": self.fullName,
            "package": self.package,
            "filename": self.filename,
            "terms": list(self.terms),
        }
