from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


GateKind = Literal[
    "operation-boundary",
    "structure-boundary",
]


@dataclass(frozen=True, slots=True)
class UnresolvedGate:
    """An unresolved boundary between two structural subjects.

    The endpoints are stable call IDs for straight-line region splits and may
    be structure group IDs for structural boundaries. A gate exists only while
    the relationship still needs resolution; resolved boundaries are expressed
    directly by the resulting phase membership.
    """

    id: str
    left_id: str
    right_id: str
    confidence: float
    evidence: tuple[str, ...] = ()
    kind: GateKind = "operation-boundary"

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("gate confidence must be between 0 and 1")


@dataclass(slots=True)
class Phase:
    """Stable phase membership and presentation metadata.

    Boundary decisions and their evidence belong to analysis gates, not to the
    phase they happen to open or extend.
    """

    nodes: list[str] = field(default_factory=list)
    id: str | None = None
    label: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Phase:
        return cls(
            nodes=list(data.get("nodes", [])),
            id=data.get("id"),
            label=data.get("label"),
        )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"nodes": list(self.nodes)}
        if self.id is not None:
            result["id"] = self.id
        if self.label is not None:
            result["label"] = self.label
        return result
