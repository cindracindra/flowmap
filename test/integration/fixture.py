"""
Shared setup for test/integration/*_test.py: builds (and caches) the CPG
for test_code/full_fixture, and hands back a live JoernSession loaded
with it. Deliberately NOT itself a test module -- these are expensive,
JVM-backed operations meant to be paid ONCE per test class (via
setUpClass), not once per test method, so they live in one place other
integration test files import from rather than each reimplementing the
caching/session lifecycle.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Pinned to __file__, not cwd -- works regardless of where the test
# runner is invoked from. Repo root (not backend/src) is what's needed
# here because backend/src/flowmap/service/cpg.py's own internal import
# (`from backend.src.flowmap.joern.util import ...`) assumes the same.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from backend.src.flowmap.joern.joern_session import JoernSession  # noqa: E402
from backend.src.flowmap.service.cpg import parse_project  # noqa: E402

# test/integration/pipeline.py -> repo root is three .parent hops up.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SOURCE_DIR = _REPO_ROOT / "test_code" / "full_fixture"
OUTPUT_DIR = Path(__file__).resolve().parent / "test_output"
CPG_PATH = OUTPUT_DIR / "cpg.bin"

FULL_CFG_SC = (
    _REPO_ROOT / "backend" / "src" / "flowmap" / "joern" / "scripts" / "full_cfg.sc"
).read_text()

# Distinct from JoernSession's default 8080 -- avoids colliding with a
# real interactive session someone might already have running on that
# port while these tests execute.
TEST_SESSION_PORT = 8099


def ensure_cpg_built() -> Path:
    """
    Parses full_fixture into a CPG if not already done, and returns the
    resulting cpg.bin path. Cached across runs, not just within one test
    session -- delete test/integration/test_output/ to force a re-parse
    after changing the fixture's .java source.
    """
    source_mtime = max(path.stat().st_mtime for path in SOURCE_DIR.rglob("*.java"))
    if not CPG_PATH.exists() or CPG_PATH.stat().st_mtime < source_mtime:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        parse_project(str(SOURCE_DIR), str(CPG_PATH))
    return CPG_PATH


def start_fixture_session() -> JoernSession:
    """
    Builds the CPG if needed, starts a fresh Joern server, and loads it.
    Caller owns the returned session and must call .stop() -- see
    setUpClass/tearDownClass in full_cfg_pipeline_test.py.

    If load_cpg fails after start() already succeeded, the session is
    stopped here before the exception propagates rather than left
    running: this function raising means the caller never gets a
    JoernSession back, and unittest does NOT call tearDownClass when
    setUpClass raises -- so without this, a failure here would leak the
    server process with nothing left to clean it up. (start() itself is
    already self-cleaning for failures during ITS OWN startup -- see
    joern_session.py -- this covers the separate step that happens after
    start() has already returned successfully.)

    Catches BaseException, not Exception: a Ctrl-C here raises
    KeyboardInterrupt, which does NOT subclass Exception (deliberately,
    so a bare `except Exception` won't swallow it) -- `except Exception`
    would let an interrupt during load_cpg skip this cleanup entirely,
    leaking the server exactly like an unhandled failure would. `raise`
    re-raises whatever it was unchanged either way.
    """
    cpg_path = ensure_cpg_built()
    session = JoernSession(port=TEST_SESSION_PORT)
    session.start()
    try:
        session.load_cpg(str(cpg_path))
    except BaseException:
        session.stop()
        raise
    return session
