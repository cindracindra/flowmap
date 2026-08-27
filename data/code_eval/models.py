from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class CodebaseStats:
    source_root: str
    java_files: int
    bytes: int
    physical_lines: int
    blank_lines: int
    comment_lines: int
    source_lines: int
    declared_types: int
    # Prefer Joern-derived counts when available. None means "not measured",
    # which is different from a measured zero.
    classes: int | None = None
    methods: int | None = None


@dataclass(frozen=True, slots=True)
class GraphStats:
    nodes: int
    edges: int
    entry_nodes: int
    call_nodes: int
    leaf_nodes: int
    exit_nodes: int
    sequence_edges: int
    invoke_edges: int
    data_edges: int
    roots: int
    orphans: int
    branch_groups: int
    loop_groups: int


@dataclass(frozen=True, slots=True)
class StageRecord:
    name: str
    started_at: str
    duration_seconds: float
    success: bool
    input_stats: dict[str, int | float | str | None] = field(default_factory=dict)
    output_stats: dict[str, int | float | str | None] = field(default_factory=dict)
    error_type: str | None = None


@dataclass(frozen=True, slots=True)
class LLMCallRecord:
    call_site: str
    provider: str
    model: str
    role: str
    duration_seconds: float
    success: bool
    prompt_characters: int
    response_characters: int
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    error_type: str | None = None


@dataclass(slots=True)
class RunRecord:
    run_id: str
    started_at: str
    codebase: CodebaseStats | None = None
    manifest: dict[str, Any] = field(default_factory=dict)
    stages: list[StageRecord] = field(default_factory=list)
    llm_calls: list[LLMCallRecord] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
