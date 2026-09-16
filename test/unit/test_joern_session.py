from __future__ import annotations

from joern.joern_session import JoernSession


def test_parse_repl_json_uses_last_valid_triple_quoted_payload():
    raw = (
        'val diagnostic: String = """not json"""\n'
        'val result: String = """{"edges": [], "stats": {}}"""\n'
    )

    assert JoernSession._parse_repl_json(raw) == {"edges": [], "stats": {}}
