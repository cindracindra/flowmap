"""
One README/markdown doc pulled from the project source tree (filesystem,
not Joern -- see domain.topic_modelling.extract_readme_documents). `package` is the
nearest enclosing Java package inferred from the doc's own directory
against the set of known ClassDocument filenames, not read from the doc
itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class ReadmeDocument:
    path: str
    package: str
    text: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ReadmeDocument:
        return cls(path=data["path"], package=data["package"], text=data["text"])

    def to_dict(self) -> dict[str, Any]:
        return {"path": self.path, "package": self.package, "text": self.text}
