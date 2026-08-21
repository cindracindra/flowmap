from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class TopicAssignment:
    # TopicCluster.label this opseq is being tied to.
    label: int 
    
    # Cosine similarity to that cluster's centroid, in [-1, 1].
    # Defaults to 1.0 for LLM-classified assignment.
    similarity: float

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TopicAssignment:
        return cls(label=data["label"], similarity=data["similarity"])

    def to_dict(self) -> dict[str, Any]:
        return {"label": self.label, "similarity": self.similarity}
