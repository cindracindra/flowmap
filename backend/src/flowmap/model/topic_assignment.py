"""
Output shape of domain.opseq_clustering.assign_operation_topics: one opseq's
proposed membership in a single TopicCluster.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class TopicAssignment:
    label: int  # TopicCluster.label this opseq is being tied to
    # Cosine similarity to that cluster's centroid, in [-1, 1] -- for an
    # LLM-classified assignment (see assign_operation_topics) this is just
    # 1.0, a placeholder marking "the model's own chosen answer" on the
    # same field the embedding path uses, so callers don't need two result
    # shapes.
    similarity: float

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TopicAssignment:
        return cls(label=data["label"], similarity=data["similarity"])

    def to_dict(self) -> dict[str, Any]:
        return {"label": self.label, "similarity": self.similarity}
