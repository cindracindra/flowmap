from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class TopicCluster:
    label: int

    # ClassDocument.fullName of every class assigned to this cluster.
    member_full_names: list[str] = field(default_factory=list)

    # c-TF-IDF top terms for this cluster, computed locally.
    statistical_terms: list[str] = field(default_factory=list)

    # Optional human-readable label from LLM call) -- None when no LLM used.
    llm_label: str | None = None

    # Paths of README/markdown docs whose inferred package overlaps this
    # cluster's member classes.
    readme_paths: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TopicCluster:
        return cls(
            label=data["label"],
            member_full_names=list(data.get("member_full_names", [])),
            statistical_terms=list(data.get("statistical_terms", [])),
            llm_label=data.get("llm_label"),
            readme_paths=list(data.get("readme_paths", [])),
        )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "label": self.label,
            "member_full_names": list(self.member_full_names),
            "statistical_terms": list(self.statistical_terms),
            "readme_paths": list(self.readme_paths),
        }
        if self.llm_label is not None:
            result["llm_label"] = self.llm_label
        return result
