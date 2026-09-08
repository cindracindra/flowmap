"""Unit tests for topic extraction, labelling, and whole-corpus fallback."""

from __future__ import annotations

import json
import io
import sys
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
sys.path.insert(
    0, str(Path(__file__).resolve().parents[2] / "backend" / "src" / "flowmap")
)

from llm.client import LLMError  # noqa: E402

from backend.src.flowmap.service import topic  # noqa: E402
from backend.src.flowmap.model import (  # noqa: E402
    ClassDocument, ReadmeDocument, TopicCluster,
)


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
                methodNames=["createAccount", "getBalance"],
            )
        }

    def test_prompt_without_documents_contains_terms_and_member_names(self):
        payload = topic.cluster_label_payload([self.cluster], None)
        item = payload["clusters"][0]
        self.assertEqual(item["representativeTerms"], ["account", "balance"])
        self.assertEqual(item["members"][0]["class"], "com.bank.account.AccountService")
        self.assertEqual(item["members"][0]["methods"], [])

    def test_prompt_contains_class_and_method_names(self):
        payload = topic.cluster_label_payload([self.cluster], self.docs)
        self.assertEqual(payload["clusters"][0]["members"], [{
            "class": "AccountService", "methods": ["createAccount", "getBalance"],
        }])

    def test_prompt_includes_every_distinct_method_name(self):
        full_name = "com.bank.account.AccountService"
        method_names = [f"method{index}" for index in range(12)]
        document = ClassDocument(
            "AccountService",
            full_name,
            "com.bank.account",
            "x.java",
            methodNames=[*method_names, "method0"],
        )

        payload = topic.cluster_label_payload([self.cluster], {full_name: document})
        prompt = json.dumps(payload)

        for method_name in method_names:
            self.assertIn(method_name, prompt)
        self.assertEqual(prompt.count("method0"), 1)

    def test_prompt_excludes_non_method_class_evidence(self):
        full_name = "com.bank.account.AccountService"
        document = ClassDocument(
            "AccountService",
            full_name,
            "com.bank.account",
            "x.java",
            methodNames=["createAccount"],
            memberNames=["accountRepository"],
            identifiers=["accountId"],
            comments=["Handles customer accounts"],
            literals=["Account not found"],
        )

        prompt = json.dumps(topic.cluster_label_payload([self.cluster], {full_name: document}))

        self.assertIn("AccountService", prompt)
        self.assertIn("createAccount", prompt)
        for excluded in (
            "accountRepository",
            "accountId",
            "Handles customer accounts",
            "Account not found",
        ):
            self.assertNotIn(excluded, prompt)

    def test_rich_prompt_skips_unknown_member(self):
        payload = topic.cluster_label_payload([self.cluster], {})
        self.assertEqual(
            payload["clusters"][0]["members"][0]["class"],
            "com.bank.account.AccountService",
        )


    def test_whole_corpus_prompt_uses_class_document_evidence(self):
        doc = ClassDocument(
            "AccountService",
            "com.bank.account.AccountService",
            "com.bank.account",
            "x.java",
            methodNames=[f"method{i}" for i in range(10)],
            memberNames=["accountMapper"],
            identifiers=["accountId"],
            comments=["Handles customer accounts"],
            literals=["Account not found"],
        )
        prompt = topic._whole_corpus_prompt([doc], [])

        self.assertIn("Methods: method0", prompt)
        self.assertIn("Members: accountMapper", prompt)
        self.assertIn("Identifiers: accountId", prompt)
        self.assertIn("Comments: Handles customer accounts", prompt)
        self.assertIn("String literals: Account not found", prompt)
        self.assertIn("method7", prompt)
        self.assertIn("method8", prompt)
        self.assertIn("method9", prompt)

    def test_whole_corpus_name_evidence_deduplicates_normalised_sequences(self):
        doc = ClassDocument(
            "AccountService",
            "com.bank.account.AccountService",
            "com.bank.account",
            "x.java",
            methodNames=[
                "getCustomerAccount",
                "get_customer_account",
                "GetCustomerAccount",
                "findUser",
            ],
            memberNames=["accountOwner", "account_owner", "balance"],
            identifiers=["CUSTOMER_ID", "customerId", "routingCode"],
        )

        prompt = topic._whole_corpus_prompt([doc], [])

        self.assertIn("Methods: getCustomerAccount, findUser", prompt)
        self.assertNotIn("get_customer_account", prompt)
        self.assertNotIn("GetCustomerAccount", prompt)
        self.assertIn("Members: accountOwner, balance", prompt)
        self.assertNotIn("account_owner", prompt)
        self.assertIn("Identifiers: CUSTOMER_ID, routingCode", prompt)
        self.assertNotIn("customerId", prompt)

    def test_whole_corpus_prose_deduplicates_complete_values_case_insensitively(self):
        doc = ClassDocument(
            "AccountService",
            "com.bank.account.AccountService",
            "com.bank.account",
            "x.java",
            comments=[
                "  Payment failed   for order  ",
                "payment FAILED for order",
                "Retries the payment later",
                "Order for failed payment",
            ],
            literals=["Account not found", "account NOT found", "Try again"],
        )

        prompt = topic._whole_corpus_prompt([doc], [])

        self.assertIn(
            "Comments: Payment failed for order, Retries the payment later, "
            "Order for failed payment",
            prompt,
        )
        self.assertNotIn("payment FAILED for order", prompt)
        self.assertIn("String literals: Account not found, Try again", prompt)
        self.assertNotIn("account NOT found", prompt)

    def test_whole_corpus_has_no_fixed_evidence_item_limit(self):
        methods = [f"operation{index}" for index in range(12)]
        doc = ClassDocument(
            "AccountService",
            "com.bank.account.AccountService",
            "com.bank.account",
            "x.java",
            methodNames=methods,
        )

        prompt = topic._whole_corpus_prompt([doc], [])

        for method in methods:
            self.assertIn(method, prompt)

    def test_whole_corpus_always_includes_every_fully_qualified_class_name(self):
        docs = [
            ClassDocument("Empty", "com.bank.Empty", "com.bank", "Empty.java"),
            ClassDocument("Tiny", "com.bank.Tiny", "com.bank", "Tiny.java"),
        ]

        prompt = topic._whole_corpus_prompt(docs, [])

        self.assertIn("Class: com.bank.Empty", prompt)
        self.assertIn("Class: com.bank.Tiny", prompt)

    def test_whole_corpus_deduplicates_readmes_and_prefers_root_document(self):
        duplicate_text = "Shared project documentation"
        readmes = [
            ReadmeDocument(
                path="src/account/README.md",
                package="com.bank.account",
                text=duplicate_text.lower(),
            ),
            ReadmeDocument(path="README.md", package="", text=duplicate_text),
        ]

        prompt = topic._whole_corpus_prompt([], readmes)

        self.assertEqual(prompt.count("Shared project documentation"), 1)
        self.assertIn("[root] Shared project documentation", prompt)
        self.assertNotIn("[com.bank.account]", prompt)

    def test_structured_evidence_caps_individual_long_values(self):
        doc = ClassDocument(
            "AccountService",
            "com.bank.account.AccountService",
            "com.bank.account",
            "x.java",
            comments=["x" * 1000],
        )

        prompt = topic._whole_corpus_prompt([doc], [])

        self.assertLess(len(prompt), 300)
        self.assertIn("…", prompt)


class LabelClusterTests(unittest.TestCase):
    def setUp(self):
        self.cluster = TopicCluster(
            label=0, member_full_names=["A"], statistical_terms=["account", "balance"]
        )

    def test_returns_model_response(self):
        client = MagicMock()
        client.complete.return_value = (
            '{"id":"0","label":"Account Management"}'
        )
        self.assertEqual(
            topic.label_clusters(client, [self.cluster]), {0: "Account Management"}
        )

        self.assertEqual(client.complete.call_args.kwargs["role"], "small")

    def test_falls_back_on_api_error(self):
        client = MagicMock()
        client.complete.side_effect = LLMError("boom")
        self.assertEqual(topic.label_clusters(client, [self.cluster]), {})
        self.assertEqual(client.complete.call_count, 3)

    def test_falls_back_on_empty_response(self):
        client = MagicMock()
        client.complete.return_value = "   "
        self.assertEqual(topic.label_clusters(client, [self.cluster]), {})
        self.assertEqual(client.complete.call_count, 3)

    def test_reports_the_rejected_label_value(self):
        client = MagicMock()
        client.complete.return_value = '{"id":"0","label":"get"}'

        with redirect_stderr(io.StringIO()) as stderr:
            self.assertEqual(topic.label_clusters(client, [self.cluster]), {})

        self.assertIn("invalid label value 'get'", stderr.getvalue())

    def test_fallback_with_no_statistical_terms(self):
        client = MagicMock()
        client.complete.side_effect = LLMError("boom")
        empty_cluster = TopicCluster(label=-1, member_full_names=["A"], statistical_terms=[])
        self.assertEqual(topic.label_clusters(client, [empty_cluster]), {})
        client.complete.assert_not_called()

    def test_batches_clusters_and_retries_only_malformed_records(self):
        clusters = [
            TopicCluster(label=index, statistical_terms=["account", "balance"])
            for index in range(3)
        ]
        client = MagicMock()
        client.complete.side_effect = [
            '{"id":"0","label":"Account Management"}\n'
            '{"id":"1","label":\n'
            '{"id":"2","label":"Payment Processing"}',
            '{"id":"1","label":"Balance Management"}',
        ]

        labels = topic.label_clusters(client, clusters)

        self.assertEqual(labels, {
            0: "Account Management",
            1: "Balance Management",
            2: "Payment Processing",
        })
        retry = json.loads(client.complete.call_args_list[1].kwargs["user"])
        self.assertEqual(
            [cluster["id"] for cluster in retry["clusters"]], ["1"]
        )
        self.assertIn("Each non-empty line must match exactly", (
            client.complete.call_args_list[0].kwargs["system"]
        ))


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
        client.complete.return_value = content

        result = topic.discover_topics_whole_corpus(client, self.docs)

        request = client.complete.call_args.kwargs
        self.assertEqual(request["role"], "large")
        self.assertTrue(request["json_object"])

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
        client.complete.return_value = content

        result = topic.discover_topics_whole_corpus(client, self.docs)
        self.assertEqual(len(result), 1)
        self.assertEqual(len(result[0].member_full_names), 3)

    def test_retries_hallucinated_class_name_not_in_corpus(self):
        invalid = (
            '{"groups": [{"label": "Account Mgmt", "member_full_names": '
            '["com.bank.account.AccountService", "com.bank.nonexistent.Fake"]}]}'
        )
        client = MagicMock()
        client.complete.side_effect = [
            invalid,
            '{"groups":[{"label":"Account Mgmt","member_full_names":'
            '["com.bank.account.AccountService"]}]}',
        ]

        result = topic.discover_topics_whole_corpus(client, self.docs)
        members = [name for cluster in result for name in cluster.member_full_names]
        self.assertNotIn("com.bank.nonexistent.Fake", members)
        self.assertEqual(client.complete.call_count, 2)

    def test_retries_invalid_whole_corpus_group_contracts(self):
        valid = (
            '{"groups":[{"label":"Account Mgmt","member_full_names":'
            '["com.bank.account.AccountService"]}]}'
        )
        invalid_responses = [
            # Additional top-level and group keys violate the exact contract.
            '{"groups":[],"extra":true}',
            '{"groups":[{"label":"Accounts","member_full_names":'
            '["com.bank.account.AccountService"],"extra":true}]}',
            # Labels and class membership must be non-empty and unique.
            '{"groups":[{"label":" ","member_full_names":'
            '["com.bank.account.AccountService"]}]}',
            '{"groups":[{"label":"Accounts","member_full_names":[]}]}',
            '{"groups":[{"label":"Accounts","member_full_names":'
            '["com.bank.account.AccountService","com.bank.account.AccountService"]}]}',
            '{"groups":[{"label":"Accounts","member_full_names":'
            '["com.bank.account.AccountService"]},{"label":"accounts",'
            '"member_full_names":["com.bank.payment.PaymentService"]}]}',
            '{"groups":[{"label":"Accounts","member_full_names":'
            '["com.bank.account.AccountService"]},{"label":"Payments",'
            '"member_full_names":["com.bank.account.AccountService"]}]}',
        ]

        for invalid in invalid_responses:
            with self.subTest(invalid=invalid):
                client = MagicMock()
                client.complete.side_effect = [invalid, valid]

                result = topic.discover_topics_whole_corpus(client, self.docs)

                self.assertEqual(client.complete.call_count, 2)
                self.assertEqual(result[0].llm_label, "Account Mgmt")

    def test_includes_readme_context_in_prompt(self):
        readmes = [ReadmeDocument(path="README.md", package="com.bank.account", text="Docs")]
        client = MagicMock()
        client.complete.return_value = '{"groups": []}'

        topic.discover_topics_whole_corpus(client, self.docs, readmes)

        sent_prompt = client.complete.call_args.kwargs["user"]
        self.assertIn("Docs", sent_prompt)

    def test_context_rejection_reports_fallback_unavailable_without_omission(self):
        client = MagicMock()
        client.complete.side_effect = LLMError("model context length exceeded")

        with self.assertRaisesRegex(
            RuntimeError,
            "fallback is unavailable.*no classes were silently omitted",
        ):
            topic.discover_topics_whole_corpus(client, self.docs)
        client.complete.assert_called_once()

    def test_uses_three_total_attempts_for_transient_request_failures(self):
        client = MagicMock()
        client.complete.side_effect = [
            LLMError("rate limited"),
            LLMError("temporary outage"),
            '{"groups": []}',
        ]

        result = topic.discover_topics_whole_corpus(client, self.docs)

        self.assertEqual(client.complete.call_count, 3)
        self.assertEqual(result[0].label, -1)
        prompts = [call.kwargs["user"] for call in client.complete.call_args_list]
        self.assertTrue(all(prompt == prompts[0] for prompt in prompts))

    def test_retries_invalid_json_within_the_same_three_attempt_policy(self):
        client = MagicMock()
        client.complete.side_effect = [
            "not json",
            '{"groups":',
            '{"groups": []}',
        ]

        result = topic.discover_topics_whole_corpus(client, self.docs)

        self.assertEqual(client.complete.call_count, 3)
        self.assertEqual(result[0].label, -1)

    def test_raises_after_three_failed_attempts(self):
        client = MagicMock()
        client.complete.side_effect = LLMError("temporary outage")

        with self.assertRaisesRegex(RuntimeError, "after 3 attempts"):
            topic.discover_topics_whole_corpus(client, self.docs)

        self.assertEqual(client.complete.call_count, 3)

    def test_raises_after_three_invalid_json_responses(self):
        client = MagicMock()
        client.complete.return_value = "not json"

        with self.assertRaisesRegex(RuntimeError, "failed after 3 attempts"):
            topic.discover_topics_whole_corpus(client, self.docs)

        self.assertEqual(client.complete.call_count, 3)


if __name__ == "__main__":
    unittest.main()
