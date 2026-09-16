from __future__ import annotations

import unittest

from fixture import SOURCE_DIR, start_fixture_session

from backend.src.flowmap.domain.topic_discovery import (
    FlowMapConfig,
    discover_topics_with_centroids,
    extract_readme_documents,
)
from backend.src.flowmap.service.topic import extract_class_documents


class TopicDiscoveryPipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.session = start_fixture_session()
        cls.class_documents = extract_class_documents(cls.session)

    @classmethod
    def tearDownClass(cls):
        cls.session.stop()

    def test_extracts_one_class_document_with_expected_evidence(self):
        self.assertEqual(len(self.class_documents), 1)
        doc = self.class_documents[0]
        self.assertEqual(doc.className, "OperationalChains")
        self.assertEqual(doc.package, "com.flowmap.fixture")
        self.assertIn("doA", doc.methodNames)
        self.assertTrue(doc.identifiers)

        self.assertTrue(any("Analyze this" in value for value in doc.literals))

    def test_readme_extraction_finds_none_in_fixture(self):

        docs = extract_readme_documents(SOURCE_DIR, self.class_documents)
        self.assertEqual(docs, [])

    def test_discover_topics_runs_end_to_end(self):
        clusters = discover_topics_with_centroids(
            self.class_documents,
            config=FlowMapConfig(min_cluster_size=2, min_samples=1),
        ).clusters
        self.assertEqual(len(clusters), 1)
        self.assertEqual(clusters[0].label, -1)
        self.assertEqual(
            clusters[0].member_full_names, ["com.flowmap.fixture.OperationalChains"]
        )
        self.assertTrue(clusters[0].statistical_terms)

    def test_discover_topics_calls_label_fn_once_with_all_clusters(self):
        calls: list[str] = []

        def label_fn(clusters):
            calls.extend(cluster.statistical_terms for cluster in clusters)
            return {cluster.label: "stub-label" for cluster in clusters}

        clusters = discover_topics_with_centroids(
            self.class_documents,
            config=FlowMapConfig(min_cluster_size=2, min_samples=1),
            label_fn=label_fn,
        ).clusters
        self.assertEqual(len(calls), 1)
        self.assertEqual(clusters[0].llm_label, "stub-label")

if __name__ == "__main__":
    unittest.main()
