"""
Output shape of service.topic.extract_class_and_method_documents' method
side -- one entry per internal method, its own scoped term bag (name/
identifiers/string-literals/internal-calls) straight from class_document.sc,
pre-preprocessing. Consumed by domain.opseq_clustering's operation-document
builder (the CFG pipeline's "entry" nodes are method-level, so they need a
method-level document to embed, not a whole-class one).
"""

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
