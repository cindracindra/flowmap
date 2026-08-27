from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend" / "src" / "flowmap"))

from joern.joern_session import JoernSession  # noqa: E402


def test_parse_repl_json_uses_last_valid_triple_quoted_payload():
    raw = (
        'val diagnostic: String = """not json"""\n'
        'val result: String = """{"edges": [], "stats": {}}"""\n'
    )

    assert JoernSession._parse_repl_json(raw) == {"edges": [], "stats": {}}
