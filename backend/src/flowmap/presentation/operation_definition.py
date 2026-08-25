from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class OperationDefinition:
    id: str
    rootEntryId: str
    label: str | None = None
    reachableMethodEntryIds: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "id": self.id,
            "rootEntryId": self.rootEntryId,
        }
        if self.label is not None:
            result["label"] = self.label
        if self.reachableMethodEntryIds:
            result["reachableMethodEntryIds"] = sorted(
                set(self.reachableMethodEntryIds)
            )
        return result

