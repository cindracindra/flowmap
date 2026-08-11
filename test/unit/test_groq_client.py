from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from backend.src.flowmap.llm import groq_client  # noqa: E402


class GetClientTests(unittest.TestCase):
    def test_missing_api_key_raises_clear_error(self):
        import os

        original = os.environ.pop("GROQ_API_KEY", None)
        try:
            with self.assertRaises(RuntimeError) as ctx:
                groq_client.get_client()
            self.assertIn("console.groq.com/keys", str(ctx.exception))
        finally:
            if original is not None:
                os.environ["GROQ_API_KEY"] = original


if __name__ == "__main__":
    unittest.main()
