from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

TransitionReason = Literal[
    "gate",
    "data-related",
    "same-callee-related",
    "data-unrelated",
    "class-related",
    "class-unrelated",
    "dead-end",
]

BoundaryType = Literal[
    "branch-entry",
    "branch-convergence",
    "semantic-split",
    "nested-region-retained",
    "uncertain-fallback",
]

DecisionSource = Literal["systematic", "llm", "fallback"]


@dataclass(slots=True)
class Transition:
    # The node this decision was evaluated against.
    subject: str | None
    reason: TransitionReason
    
    # 0 for "dead-end" (never went through the cascade at all),
    # otherwise which cascade level decided it.
    level: Literal[0, 1, 2, 3]

    # Optional richer boundary evidence for the new resolver. Left absent by
    # the current cascade so existing phase JSON remains backward compatible.
    boundaryType: BoundaryType | None = None
    decidedBy: DecisionSource | None = None
    confidence: float | None = None
    evidence: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Transition:
        return cls(
            subject=data.get("subject"),
            reason=data["reason"],
            level=data["level"],
            boundaryType=data.get("boundaryType"),
            decidedBy=data.get("decidedBy"),
            confidence=data.get("confidence"),
            evidence=list(data.get("evidence", [])),
        )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"reason": self.reason, "level": self.level}
        if self.subject is not None:
            result["subject"] = self.subject
        if self.boundaryType is not None:
            result["boundaryType"] = self.boundaryType
        if self.decidedBy is not None:
            result["decidedBy"] = self.decidedBy
        if self.confidence is not None:
            result["confidence"] = self.confidence
        if self.evidence:
            result["evidence"] = list(self.evidence)
        return result


@dataclass(slots=True)
class Phase:
    nodes: list[str] = field(default_factory=list)

    # Stable internal identity and user-facing semantic label are populated by
    # the new labelling pipeline. Optional until that pipeline replaces the
    # current ordinal-only output.
    id: str | None = None
    label: str | None = None
    structuralAnchors: list[str] = field(default_factory=list)

    # Why this phase's first node split off from whatever preceded it.
    # None for a trace's very first phase.
    opened_by: Transition | None = None

    # Why each later node joined this phase, one entry per merge.
    transitions: list[Transition] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Phase:
        opened_by = data.get("opened_by")
        return cls(
            nodes=list(data.get("nodes", [])),
            id=data.get("id"),
            label=data.get("label"),
            structuralAnchors=list(data.get("structuralAnchors", [])),
            opened_by=Transition.from_dict(opened_by) if opened_by else None,
            transitions=[Transition.from_dict(t) for t in data.get("transitions", [])],
        )

    def to_dict(self) -> dict[str, Any]:
        result = {
            "nodes": list(self.nodes),
            "opened_by": self.opened_by.to_dict() if self.opened_by else None,
            "transitions": [t.to_dict() for t in self.transitions],
        }
        if self.id is not None:
            result["id"] = self.id
        if self.label is not None:
            result["label"] = self.label
        if self.structuralAnchors:
            result["structuralAnchors"] = list(self.structuralAnchors)
        return result
