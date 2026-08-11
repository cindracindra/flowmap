"""
Unit tests for service/topic.py's Groq-backed functions (label_cluster,
discover_topics_whole_corpus, _cluster_prompt) -- moved here from
test_groq_client.py when those functions moved out of llm/groq_client.py.
The Groq client itself is always a MagicMock, never a real API call, so
these stay fast/hermetic despite exercising "service" code -- see
service/topic.py's own module docstring for why extract_class_documents
(Joern) and these (Groq) are kept in one file organized by domain concept
rather than split by which external system each needs.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from groq import GroqError  # noqa: E402

from backend.src.flowmap.service import topic  # noqa: E402
from backend.src.flowmap.model import ClassDocument, ReadmeDocument, TopicCluster  # noqa: E402


def _mock_response(content: str) -> MagicMock:
    response = MagicMock()
    response.choices = [MagicMock(message=MagicMock(content=content))]
    return response


class ClusterPromptTests(unittest.TestCase):
    def setUp(self):
        self.cluster = TopicCluster(
            label=0,
            member_full_names=["com.bank.account.AccountService"],
            statistical_terms=["account", "balance"],
        )
        self.docs = {
            "com.bank.account.AccountService": ClassDocument(
                "AccountService",
                "com.bank.account.AccountService",
                "com.bank.account",
                "x.java",
                ["AccountService", "createAccount", "getBalance", "AccountRepository"],
            )
        }

    def test_thin_prompt_omits_per_class_terms(self):
        prompt = topic._cluster_prompt(self.cluster, None)
        self.assertIn("account, balance", prompt)
        self.assertNotIn("createAccount", prompt)

    def test_rich_prompt_includes_member_terms_but_not_class_name_itself(self):
        prompt = topic._cluster_prompt(self.cluster, self.docs)
        self.assertIn("createAccount", prompt)
        self.assertIn("getBalance", prompt)
        # doc.terms[0] is the class name itself, deliberately excluded --
        # it's already shown separately via member_full_names.
        self.assertNotIn("terms: AccountService,", prompt)

    def test_rich_prompt_skips_unknown_member(self):
        prompt = topic._cluster_prompt(self.cluster, {})
        self.assertIn("Classes: com.bank.account.AccountService", prompt)


class LabelClusterTests(unittest.TestCase):
    def setUp(self):
        self.cluster = TopicCluster(
            label=0, member_full_names=["A"], statistical_terms=["account", "balance"]
        )

    def test_returns_model_response(self):
        client = MagicMock()
        client.chat.completions.create.return_value = _mock_response("Account Management")
        self.assertEqual(topic.label_cluster(client, self.cluster), "Account Management")

    def test_falls_back_on_api_error(self):
        client = MagicMock()
        client.chat.completions.create.side_effect = GroqError("boom")
        self.assertEqual(topic.label_cluster(client, self.cluster), "account")

    def test_falls_back_on_empty_response(self):
        client = MagicMock()
        client.chat.completions.create.return_value = _mock_response("   ")
        self.assertEqual(topic.label_cluster(client, self.cluster), "account")

    def test_fallback_with_no_statistical_terms(self):
        client = MagicMock()
        client.chat.completions.create.side_effect = GroqError("boom")
        empty_cluster = TopicCluster(label=-1, member_full_names=["A"], statistical_terms=[])
        self.assertEqual(topic.label_cluster(client, empty_cluster), "unlabeled")


class DiscoverTopicsWholeCorpusTests(unittest.TestCase):
    def setUp(self):
        self.docs = [
            ClassDocument(
                "AccountService", "com.bank.account.AccountService", "com.bank.account",
                "x.java", ["AccountService", "createAccount"],
            ),
            ClassDocument(
                "PaymentService", "com.bank.payment.PaymentService", "com.bank.payment",
                "y.java", ["PaymentService", "processPayment"],
            ),
            ClassDocument(
                "Orphan", "com.bank.misc.Orphan", "com.bank.misc", "z.java", ["Orphan"],
            ),
        ]

    def test_parses_groups_and_collects_unassigned_into_noise(self):
        content = (
            '{"groups": ['
            '{"label": "Account Mgmt", "member_full_names": ["com.bank.account.AccountService"]}, '
            '{"label": "Payments", "member_full_names": ["com.bank.payment.PaymentService"]}'
            "]}"
        )
        client = MagicMock()
        client.chat.completions.create.return_value = _mock_response(content)

        result = topic.discover_topics_whole_corpus(client, self.docs)

        self.assertEqual(len(result), 3)
        self.assertEqual(result[0].label, 0)
        self.assertEqual(result[0].member_full_names, ["com.bank.account.AccountService"])
        self.assertEqual(result[0].llm_label, "Account Mgmt")
        self.assertEqual(result[1].label, 1)
        self.assertEqual(result[2].label, -1)
        self.assertEqual(result[2].member_full_names, ["com.bank.misc.Orphan"])

    def test_strips_markdown_json_fence(self):
        content = (
            "```json\n"
            '{"groups": [{"label": "Everything", "member_full_names": '
            '["com.bank.account.AccountService", "com.bank.payment.PaymentService", '
            '"com.bank.misc.Orphan"]}]}\n'
            "```"
        )
        client = MagicMock()
        client.chat.completions.create.return_value = _mock_response(content)

        result = topic.discover_topics_whole_corpus(client, self.docs)
        self.assertEqual(len(result), 1)
        self.assertEqual(len(result[0].member_full_names), 3)

    def test_drops_hallucinated_class_name_not_in_corpus(self):
        content = (
            '{"groups": [{"label": "Account Mgmt", "member_full_names": '
            '["com.bank.account.AccountService", "com.bank.nonexistent.Fake"]}]}'
        )
        client = MagicMock()
        client.chat.completions.create.return_value = _mock_response(content)

        result = topic.discover_topics_whole_corpus(client, self.docs)
        members = [name for cluster in result for name in cluster.member_full_names]
        self.assertNotIn("com.bank.nonexistent.Fake", members)

    def test_includes_readme_context_in_prompt(self):
        readmes = [ReadmeDocument(path="README.md", package="com.bank.account", text="Docs")]
        client = MagicMock()
        client.chat.completions.create.return_value = _mock_response('{"groups": []}')

        topic.discover_topics_whole_corpus(client, self.docs, readmes)

        sent_prompt = client.chat.completions.create.call_args.kwargs["messages"][1]["content"]
        self.assertIn("Docs", sent_prompt)


if __name__ == "__main__":
    unittest.main()
