from pathlib import Path

from joern.joern_session import JoernSession
from model import Graph

_FULL_CFG_SC = (
    Path(__file__).parent.parent / "joern/scripts/full_cfg.sc"
).read_text()


def extract_full_cfg(session: JoernSession) -> Graph:
    """
    Single-pass, whole-codebase inter-method CFG (full_cfg.sc).
    Returns a Graph with entryPoint=None -- pair with
    classify_roots_and_orphans to populate `roots`/`orphans`.
    """
    return Graph.from_dict(session.query_script_json(_FULL_CFG_SC))