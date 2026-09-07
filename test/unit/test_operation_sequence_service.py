from __future__ import annotations

import io
import json
import sys
import threading
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
sys.path.insert(
    0, str(Path(__file__).resolve().parents[2] / "backend" / "src" / "flowmap")
)

from llm.client import LLMError

from backend.src.flowmap.model import Graph, MethodDocument, Node, TopicCluster
from backend.src.flowmap.service import operation_sequence


class ClassifyOperationTests(unittest.TestCase):
    def test_retries_invalid_json_with_three_total_attempts(self):
        client = MagicMock()
        client.complete.side_effect = [
            "not json",
            '{"group_id":',
            '{"id":"operation","group_id":1}',
        ]
        graph = Graph(entryPoint="Order.checkout")
        clusters = [TopicCluster(label=1, llm_label="Order Processing")]

        result = operation_sequence.classify_operation_topics(
            client, {"operation": graph}, clusters
        )

        self.assertEqual(result, {"operation": 1})
        self.assertEqual(client.complete.call_count, 3)

    def test_retries_only_the_malformed_operation(self):
        payloads = []

        class PartialClient:
            def complete(self, **kwargs):
                payload = json.loads(kwargs["user"])
                payloads.append(payload)
                if len(payloads) == 1:
                    return (
                        '{"id":"valid","group_id":1}\n'
                        '{"id":"invalid","group_id":"wrong"}'
                    )
                return '{"id":"invalid","group_id":null}'

        operations = {"valid": Graph(), "invalid": Graph()}
        result = operation_sequence.classify_operation_topics(
            PartialClient(), operations, [TopicCluster(label=1)]
        )

        self.assertEqual(result, {"valid": 1, "invalid": None})
        self.assertEqual(
            [[item["id"] for item in payload["operations"]] for payload in payloads],
            [["valid", "invalid"], ["invalid"]],
        )


class OpseqLabelConcurrencyTests(unittest.TestCase):
    def test_shared_methods_are_catalogued_once_and_referenced(self):
        payloads = []

        class CapturingClient:
            def complete(self, **kwargs):
                payload = json.loads(kwargs["user"])
                payloads.append((payload, kwargs))
                return "\n".join(
                    json.dumps({"id": operation["id"], "label": f"Handle Item {index}"})
                    for index, operation in enumerate(payload["operations"])
                )

        cluster = TopicCluster(label=1)
        operations = {
            "first": (Graph(nodes=[
                Node("a", "entry", calleeFullName="Shared.run"),
                Node("b", "entry", calleeFullName="First.run"),
            ]), cluster),
            "second": (Graph(nodes=[
                Node("c", "entry", calleeFullName="Shared.run"),
                Node("d", "entry", calleeFullName="Second.run"),
            ]), cluster),
        }
        documents = [
            MethodDocument("run", "Shared.run", ["common", "work"]),
            MethodDocument("run", "First.run", ["create", "item"]),
            MethodDocument("run", "Second.run", ["delete", "item"]),
        ]

        operation_sequence.label_operation_sequences_with_llm(
            CapturingClient(), operations
        )

        payload, kwargs = payloads[0]
        self.assertEqual(
            payload["operations"],
            [
                {
                    "id": "first",
                    "entryPoint": None,
                    "subsequentMethods": ["Shared.run", "First.run"],
                },
                {
                    "id": "second",
                    "entryPoint": None,
                    "subsequentMethods": ["Shared.run", "Second.run"],
                },
            ],
        )
        self.assertNotIn("methodEvidence", payload)
        self.assertNotIn("commonMethodRefs", payload)
        self.assertEqual(kwargs["max_tokens"], 2048)
        self.assertNotIn("json_object", kwargs)

    def test_operations_are_batched_by_topic_with_four_workers(self):
        barrier = threading.Barrier(4)
        lock = threading.Lock()
        active = 0
        peak_active = 0
        request_sizes = []

        class ConcurrentClient:
            def complete(self, **kwargs):
                nonlocal active, peak_active
                payload = json.loads(kwargs["user"])
                with lock:
                    active += 1
                    peak_active = max(peak_active, active)
                    request_sizes.append(len(payload["operations"]))
                try:
                    barrier.wait(timeout=2)
                    return "\n".join(
                        json.dumps({"id": operation["id"], "label": f"Operation {index}"})
                        for index, operation in enumerate(payload["operations"])
                    )
                finally:
                    with lock:
                        active -= 1

        clusters = [TopicCluster(label=index) for index in range(4)]
        operations = {
            f"root-{index}": (
                Graph(entryPoint=f"method-{index}"), clusters[index // 2]
            )
            for index in range(8)
        }

        result = operation_sequence.label_operation_sequences_with_llm(
            ConcurrentClient(), operations
        )

        self.assertEqual(list(result), list(operations))
        self.assertEqual(sorted(request_sizes), [2, 2, 2, 2])
        self.assertEqual(
            list(result.values()), ["Operation 0", "Operation 1"] * 4
        )
        self.assertEqual(peak_active, 4)

    def test_large_topic_uses_sequential_operation_bounded_sub_batches(self):
        requests = []

        class BatchClient:
            def complete(self, **kwargs):
                payload = json.loads(kwargs["user"])
                requests.append(payload)
                return "\n".join(
                    json.dumps({"id": operation["id"], "label": f"Work Item {operation['id']}"})
                    for operation in payload["operations"]
                )

        cluster = TopicCluster(label=1)
        operations = {str(index): (Graph(), cluster) for index in range(70)}

        result = operation_sequence.label_operation_sequences_with_llm(BatchClient(), operations)

        self.assertEqual(len(result), 70)
        self.assertEqual(
            [len(request["operations"]) for request in requests], [32, 32, 6]
        )
        self.assertEqual(len(requests[1]["reservedLabels"]), 32)
        self.assertEqual(len(requests[2]["reservedLabels"]), 64)

    def test_failed_batch_progressively_isolates_from_twelve_to_four_to_one(self):
        request_sizes = []

        class ProgressiveClient:
            def complete(self, **kwargs):
                payload = json.loads(kwargs["user"])
                request_sizes.append(len(payload["operations"]))
                if len(payload["operations"]) > 1:
                    return ""
                operation_id = payload["operations"][0]["id"]
                return json.dumps({"id": operation_id, "label": f"Work Item {operation_id}"})

        cluster = TopicCluster(label=1)
        operations = {
            str(index): (Graph(), cluster) for index in range(6)
        }

        result = operation_sequence.label_operation_sequences_with_llm(
            ProgressiveClient(), operations
        )

        self.assertTrue(all(result.values()))
        self.assertEqual(request_sizes, [6, 6, 1, 1, 1, 1, 1, 1])

    def test_context_overflow_splits_sub_batch_instead_of_repeating_it(self):
        request_sizes = []

        class SplittingClient:
            def complete(self, **kwargs):
                payload = json.loads(kwargs["user"])
                request_sizes.append(len(payload["operations"]))
                if len(payload["operations"]) == 4:
                    raise LLMError(
                        "input is longer than the model's context length"
                    )
                return "\n".join(
                    json.dumps({"id": operation["id"], "label": f"Work Item {operation['id']}"})
                    for operation in payload["operations"]
                )

        cluster = TopicCluster(label=1)
        operations = {
            str(index): (Graph(), cluster) for index in range(4)
        }
        stderr = io.StringIO()

        with redirect_stderr(stderr):
            result = operation_sequence.label_operation_sequences_with_llm(
                SplittingClient(), operations
            )

        self.assertEqual(len(result), 4)
        self.assertEqual(request_sizes, [4, 2, 2])
        self.assertIn("instead of retrying unchanged", stderr.getvalue())

    def test_one_failed_topic_does_not_discard_other_topic_results(self):
        good_topic = TopicCluster(label=1)
        bad_topic = TopicCluster(label=2)
        operations = {
            "good": (Graph(entryPoint="good"), good_topic),
            "bad": (Graph(entryPoint="bad"), bad_topic),
        }

        def complete(**kwargs):
            if json.loads(kwargs["user"])["topic"]["id"] == 1:
                return '{"id":"good","label":"Good Label"}'
            raise LLMError("boom")

        client = MagicMock()
        client.complete.side_effect = complete
        self.assertEqual(
            operation_sequence.label_operation_sequences_with_llm(client, operations),
            {"good": "Good Label", "bad": None},
        )

    def test_duplicate_labels_within_topic_are_rejected(self):
        client = MagicMock()
        client.complete.return_value = (
            '{"id":"first","label":"Create Account"}\n'
            '{"id":"second","label":"create account"}'
        )
        cluster = TopicCluster(label=1)

        result = operation_sequence.label_operation_sequences_with_llm(client, {
            "first": (Graph(), cluster),
            "second": (Graph(), cluster),
        })

        self.assertEqual(result, {"first": "Create Account", "second": None})
        request = client.complete.call_args.kwargs
        self.assertNotIn("json_object", request)
        self.assertEqual(request["max_tokens"], 2048)
        self.assertIn("unique within this request", request["system"])

    def test_legacy_line_response_is_rejected(self):
        client = MagicMock()
        client.complete.return_value = (
            "first: Create Account\n"
            "this line has no tab\n"
            "second: Close Account"
        )
        cluster = TopicCluster(label=1)
        stderr = io.StringIO()

        with redirect_stderr(stderr):
            result = operation_sequence.label_operation_sequences_with_llm(client, {
                "first": (Graph(), cluster),
                "second": (Graph(), cluster),
            })

        self.assertEqual(result, {"first": None, "second": None})
        self.assertEqual(client.complete.call_count, 4)
        self.assertIn("malformed, duplicate, or missing", stderr.getvalue())

    def test_json_lines_retries_only_the_malformed_operation(self):
        client = MagicMock()
        client.complete.side_effect = [
            '{"id":"first","label":"Create Account"}\n'
            '{"id":"second","label":',
            '{"id":"second","label":"Close Account"}',
        ]
        cluster = TopicCluster(label=1)

        result = operation_sequence.label_operation_sequences_with_llm(client, {
            "first": (Graph(), cluster),
            "second": (Graph(), cluster),
        })

        self.assertEqual(result, {
            "first": "Create Account", "second": "Close Account",
        })
        retry = json.loads(client.complete.call_args_list[1].kwargs["user"])
        self.assertEqual(
            [operation["id"] for operation in retry["operations"]], ["second"]
        )

    def test_concatenated_json_objects_are_retained_without_retry(self):
        client = MagicMock()
        client.complete.return_value = (
            '{"id":"first","label":"Create Account"}'
            '{"id":"second","label":"Close Account"}'
        )
        cluster = TopicCluster(label=1)

        result = operation_sequence.label_operation_sequences_with_llm(client, {
            "first": (Graph(), cluster),
            "second": (Graph(), cluster),
        })

        self.assertEqual(
            result, {"first": "Create Account", "second": "Close Account"}
        )
        self.assertEqual(client.complete.call_count, 1)
        report = client.record_batch_report.call_args.args[0]
        self.assertEqual(report["parse_failures"], {"concatenated_json": 1})

    def test_noncanonical_legacy_separators_are_rejected(self):
        client = MagicMock()
        client.complete.return_value = (
            "first\\tCreate Account\n"
            "second<TAB>Close Account\n"
            "m123   Read JSON Value"
        )
        cluster = TopicCluster(label=1)
        stderr = io.StringIO()

        with redirect_stderr(stderr):
            result = operation_sequence.label_operation_sequences_with_llm(client, {
                "first": (Graph(), cluster),
                "second": (Graph(), cluster),
                "m123": (Graph(), cluster),
        })

        self.assertEqual(result, {"first": None, "second": None, "m123": None})
        system = client.complete.call_args.kwargs["system"]
        self.assertIn("OUTPUT FORMAT IS A STRICT CONTRACT", system)
        self.assertIn('"id":"exact-operation-id"', system)
        self.assertIn("JSON Lines", system)
        self.assertIn("malformed, duplicate, or missing", stderr.getvalue())

    def test_malformed_responses_are_retried_and_include_a_preview(self):
        client = MagicMock()
        client.complete.side_effect = [
            '{"labels" []}',
            "first\tCreate Account",
            '{"id":"first","label":"Create Account"}',
        ]
        cluster = TopicCluster(label=1)
        stderr = io.StringIO()

        with redirect_stderr(stderr):
            result = operation_sequence.label_operation_sequences_with_llm(client, {
                "first": (Graph(), cluster),
        })

        self.assertEqual(result, {"first": "Create Account"})
        self.assertIn("preview=", stderr.getvalue())
        self.assertIn('{"labels" []}', stderr.getvalue())

    def test_missing_label_is_reported_and_retried_with_reserved_labels(self):
        client = MagicMock()
        client.complete.side_effect = [
            '{"id":"first","label":"Create Account"}',
            '{"id":"second","label":"Close Account"}',
        ]
        cluster = TopicCluster(label=1)
        stderr = io.StringIO()

        with redirect_stderr(stderr):
            result = operation_sequence.label_operation_sequences_with_llm(client, {
                "first": (Graph(), cluster),
                "second": (Graph(), cluster),
        })

        self.assertEqual(result, {
            "first": "Create Account", "second": "Close Account",
        })
        retry = json.loads(client.complete.call_args_list[1].kwargs["user"])
        self.assertEqual(retry["reservedLabels"], {"first": "Create Account"})
        self.assertEqual([item["id"] for item in retry["operations"]], ["second"])
        self.assertIn("left 1 operation(s) unresolved", stderr.getvalue())
        self.assertIn("retrying", stderr.getvalue())
