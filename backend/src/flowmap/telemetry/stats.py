from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

from .models import CodebaseStats, GraphStats

_TYPE_DECLARATION = re.compile(r"\b(?:class|interface|enum|record)\s+[A-Za-z_$][\w$]*")


def _line_counts(text: str) -> tuple[int, int, int, int]:
    """Return physical, blank, comment, and source lines.

    This small scanner is reproducible and dependency-free. `source_lines` is
    an evaluation proxy, not a replacement for a published LOC tool such as
    cloc; block-comment lines containing code are conservatively counted as
    comments.
    """
    physical = blank = comments = source = 0
    in_block_comment = False
    for line in text.splitlines():
        physical += 1
        stripped = line.strip()
        if not stripped:
            blank += 1
            continue
        if in_block_comment:
            comments += 1
            if "*/" in stripped:
                in_block_comment = False
            continue
        if stripped.startswith("//"):
            comments += 1
        elif stripped.startswith("/*"):
            comments += 1
            in_block_comment = "*/" not in stripped[2:]
        else:
            source += 1
    return physical, blank, comments, source


def collect_codebase_stats(
    source_root: str | Path,
    *,
    class_documents: Iterable[object] | None = None,
    method_documents: Iterable[object] | None = None,
) -> CodebaseStats:
    root = Path(source_root).resolve()
    files = sorted(path for path in root.rglob("*.java") if path.is_file())
    byte_count = physical = blank = comments = source = declared_types = 0
    for path in files:
        raw = path.read_bytes()
        text = raw.decode("utf-8", errors="replace")
        byte_count += len(raw)
        counts = _line_counts(text)
        physical += counts[0]
        blank += counts[1]
        comments += counts[2]
        source += counts[3]
        declared_types += len(_TYPE_DECLARATION.findall(text))
    classes = None if class_documents is None else sum(1 for _ in class_documents)
    methods = None if method_documents is None else sum(1 for _ in method_documents)
    return CodebaseStats(
        source_root=str(root),
        java_files=len(files),
        bytes=byte_count,
        physical_lines=physical,
        blank_lines=blank,
        comment_lines=comments,
        source_lines=source,
        declared_types=declared_types,
        classes=classes,
        methods=methods,
    )


def collect_graph_stats(graph: object) -> GraphStats:
    nodes = list(getattr(graph, "nodes"))
    edges = list(getattr(graph, "edges"))
    node_types = [getattr(node, "type", None) for node in nodes]
    edge_types = [getattr(edge, "type", None) for edge in edges]
    return GraphStats(
        nodes=len(nodes), edges=len(edges),
        entry_nodes=node_types.count("entry"), call_nodes=node_types.count("call"),
        leaf_nodes=node_types.count("leaf"), exit_nodes=node_types.count("exit"),
        sequence_edges=edge_types.count("sequence"), invoke_edges=edge_types.count("invoke"),
        data_edges=edge_types.count("data"),
        roots=len(getattr(graph, "roots", ())), orphans=len(getattr(graph, "orphans", ())),
        branch_groups=len(getattr(graph, "branchGroups", ())),
        loop_groups=len(getattr(graph, "loopGroups", ())),
    )
