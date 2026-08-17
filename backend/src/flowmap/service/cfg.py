import dataclasses
from pathlib import Path

from joern.joern_session import JoernSession
from model import Graph

_FULL_CFG_SC = (
    Path(__file__).parent.parent / "joern/scripts/full_cfg.sc"
).read_text()


def _relative_source_file(filename: str, source_dir: Path) -> str | None:
    if not filename.strip() or filename.startswith("<"):
        return None
    path = Path(filename)
    if path.is_absolute():
        try:
            path = path.relative_to(source_dir)
        except ValueError:
            pass
    return path.as_posix()


def extract_full_cfg(session: JoernSession, source_dir: str | Path | None = None) -> Graph:
    """
    Single-pass, whole-codebase inter-method CFG (full_cfg.sc).
    Returns a Graph with entryPoint=None -- pair with
    classify_roots_and_orphans to populate `roots`/`orphans`.
    """
    graph = Graph.from_dict(session.query_script_json(_FULL_CFG_SC))
    if source_dir is None:
        return graph

    root = Path(source_dir).resolve()
    return dataclasses.replace(
        graph,
        nodes=[
            dataclasses.replace(
                node,
                sourceFile=_relative_source_file(node.sourceFile, root),
            ) if node.sourceFile else node
            for node in graph.nodes
        ],
    )
