from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from backend.src.flowmap.domain.opseq_clustering import (  # noqa: E402
    assign_operation_topics_batch,
    build_operation_document,
)
from backend.src.flowmap.model import Graph, MethodDocument, Node, TopicCluster  # noqa: E402


def _operation(operation_id: str, method_name: str) -> Graph:
    return Graph(
        entryPoint=method_name,
        nodes=[Node(id=operation_id, type="entry", calleeFullName=method_name)],
    )


class BuildOperationDocumentTests(unittest.TestCase):
    def test_repeated_entry_method_is_included_once(self):
        method_name = "pkg.Account.create:void()"
        graph = Graph(
            nodes=[
                Node(id="one", type="entry", calleeFullName=method_name),
                Node(id="two", type="entry", calleeFullName=method_name),
            ]
        )
        methods = [MethodDocument(method_name, ["create", "accountId"])]

        self.assertEqual(build_operation_document(graph, methods), "create account")


class BatchAssignmentTests(unittest.TestCase):
    @patch("backend.src.flowmap.domain.opseq_clustering.embed_documents")
    def test_embeds_all_operations_in_one_batch(self, mock_embed):
        mock_embed.return_value = np.array([[1.0, 0.0], [0.0, 1.0]])
        operations = {
            "op-account": _operation("op-account", "pkg.Account.create:void()"),
            "op-order": _operation("op-order", "pkg.Order.submit:void()"),
        }
        methods = [
            MethodDocument("pkg.Account.create:void()", ["create", "account"]),
            MethodDocument("pkg.Order.submit:void()", ["submit", "order"]),
        ]
        clusters = [
            TopicCluster(label=10, member_full_names=["pkg.Account"]),
            TopicCluster(label=20, member_full_names=["pkg.Order"]),
        ]

        result = assign_operation_topics_batch(
            operations,
            clusters,
            methods,
            {10: np.array([1.0, 0.0]), 20: np.array([0.0, 1.0])},
        )

        mock_embed.assert_called_once()
        self.assertEqual(result["op-account"][0].label, 10)
        self.assertEqual(result["op-order"][0].label, 20)
        self.assertEqual(len(result["op-account"]), 1)
        self.assertEqual(len(result["op-order"]), 1)


if __name__ == "__main__":
    unittest.main()
