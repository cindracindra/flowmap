from __future__ import annotations

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]

from backend.src.flowmap.joern.joern_session import JoernSession
from backend.src.flowmap.service.cpg import parse_project

SOURCE_DIR = _REPO_ROOT / "test_code" / "full_fixture"
OUTPUT_DIR = Path(__file__).resolve().parent / "test_output"
CPG_PATH = OUTPUT_DIR / "cpg.bin"

FULL_CFG_SC = (
    _REPO_ROOT / "backend" / "src" / "flowmap" / "joern" / "scripts" / "full_cfg.sc"
).read_text()

TEST_SESSION_PORT = 8099


def ensure_cpg_built() -> Path:
    source_mtime = max(path.stat().st_mtime for path in SOURCE_DIR.rglob("*.java"))
    if not CPG_PATH.exists() or CPG_PATH.stat().st_mtime < source_mtime:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        parse_project(str(SOURCE_DIR), str(CPG_PATH))
    return CPG_PATH


def start_fixture_session() -> JoernSession:
    cpg_path = ensure_cpg_built()
    session = JoernSession(port=TEST_SESSION_PORT)
    session.start()
    try:
        session.load_cpg(str(cpg_path))
    except BaseException:
        session.stop()
        raise
    return session
