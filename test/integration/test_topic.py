"""
Integration test: runs the REAL pipeline (joern-parse -> class_document.sc
over a live Joern server -> discover_topics_with_centroids) against
test_code/full_fixture.

Shape-only assertions -- full_fixture has exactly one class, so HDBSCAN
(which needs at least min_cluster_size members to form a cluster at all)
can only ever put it in the noise bucket (-1); this test exists to prove
the Joern-extraction -> embedding -> clustering -> labeling wiring runs
end to end without error, not to assert a particular clustering outcome.
Downloads the sentence-transformers model on first run (see
domain.topic_discovery.clustering.embed_documents) -- needs network access once, then cached.

Slow (JVM server boot, model download on first run) -- see fixture.py's
own note on running this in isolation:

    poetry run python -m unittest discover -s test/integration -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from fixture import SOURCE_DIR, start_fixture_session  # noqa: E402

from backend.src.flowmap.domain.topic_discovery import (  # noqa: E402
    FlowMapConfig,
    discover_topics_with_centroids,
    extract_readme_documents,
)
from backend.src.flowmap.service.topic import extract_class_documents  # noqa: E402


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
        # String-literal content, quotes stripped (see class_document.sc's
        # stripQuotes) -- confirms the LITERAL.code extraction is wired.
        self.assertTrue(any("Analyze this" in value for value in doc.literals))

    def test_readme_extraction_finds_none_in_fixture(self):
        # full_fixture has no README/markdown docs -- this only checks the
        # filesystem walk runs cleanly against a real source tree.
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
