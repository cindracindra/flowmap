from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from backend.src.flowmap.llm import client as llm_client  # noqa: E402


class GetClientTests(unittest.TestCase):
    def _without(self, name: str):
        original = os.environ.pop(name, None)
        self.addCleanup(
            lambda: os.environ.__setitem__(name, original)
            if original is not None
            else None
        )

    def test_missing_groq_key_raises_clear_error(self):
        self._without("GROQ_API_KEY")
        with self.assertRaises(RuntimeError) as ctx:
            llm_client.get_client("groq")
        self.assertIn("console.groq.com/keys", str(ctx.exception))

    def test_missing_together_key_raises_clear_error(self):
        self._without("TOGETHERAI_API_KEY")
        with self.assertRaises(RuntimeError) as ctx:
            llm_client.get_client("together")
        self.assertIn("TOGETHERAI_API_KEY", str(ctx.exception))

    def test_unknown_provider_rejected(self):
        with self.assertRaises(ValueError):
            llm_client.get_client("openai")


class CompleteTests(unittest.TestCase):
    def _client(self, provider: str) -> tuple[llm_client.LLMClient, MagicMock]:
        sdk = MagicMock()
        sdk.chat.completions.create.return_value.choices = [
            MagicMock(message=MagicMock(content="a label"))
        ]
        return llm_client.LLMClient(provider=provider, sdk=sdk), sdk

    def test_groq_uses_max_completion_tokens_and_suppresses_reasoning(self):
        client, sdk = self._client("groq")
        self.assertEqual(
            client.complete(role="small", system="s", user="u", max_tokens=64),
            "a label",
        )
        kwargs = sdk.chat.completions.create.call_args.kwargs
        self.assertEqual(kwargs["max_completion_tokens"], 64)
        self.assertIs(kwargs["include_reasoning"], False)
        self.assertNotIn("max_tokens", kwargs)
        self.assertEqual(kwargs["model"], "openai/gpt-oss-20b")

    def test_together_uses_max_tokens_and_omits_groq_only_flag(self):
        client, sdk = self._client("together")
        client.complete(role="large", system="s", user="u", max_tokens=64)
        kwargs = sdk.chat.completions.create.call_args.kwargs
        self.assertEqual(kwargs["max_tokens"], 64)
        self.assertNotIn("include_reasoning", kwargs)
        self.assertNotIn("max_completion_tokens", kwargs)
        self.assertEqual(kwargs["model"], "openai/gpt-oss-120b")

    def test_json_object_sets_response_format(self):
        client, sdk = self._client("groq")
        client.complete(role="small", system="s", user="u", json_object=True)
        kwargs = sdk.chat.completions.create.call_args.kwargs
        self.assertEqual(kwargs["response_format"], {"type": "json_object"})

    def test_none_content_becomes_empty_string(self):
        client, sdk = self._client("groq")
        sdk.chat.completions.create.return_value.choices = [
            MagicMock(message=MagicMock(content=None))
        ]
        self.assertEqual(client.complete(role="small", system="s", user="u"), "")

    def test_provider_error_is_normalised(self):
        from openai import OpenAIError

        client, sdk = self._client("together")
        sdk.chat.completions.create.side_effect = OpenAIError("boom")
        with self.assertRaises(llm_client.LLMError):
            client.complete(role="small", system="s", user="u")


if __name__ == "__main__":
    unittest.main()
