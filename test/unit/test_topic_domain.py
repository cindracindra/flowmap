from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np

# Repo root -- test/unit/ is two levels below it (see test_cfg.py's own
# note on this same insertion).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from backend.src.flowmap.domain.topic_modelling import (  # noqa: E402
    attach_readme_context,
    cluster_documents,
    discover_topics,
    discover_topics_with_centroids,
    extract_readme_documents,
    is_degenerate,
    label_clusters_statistical,
)
from backend.src.flowmap.domain.util import (  # noqa: E402
    embed_documents,
    get_embedding_model,
    is_noise,
    preprocess_document,
    split_identifier,
)
from backend.src.flowmap.model import ClassDocument, ReadmeDocument, TopicCluster  # noqa: E402


class SplitIdentifierTests(unittest.TestCase):
    def test_plain_camel_case(self):
        self.assertEqual(split_identifier("getUserById"), ["get", "user", "by", "id"])

    def test_non_dictionary_identifier_wordninja_used_to_mishandle(self):
        # The exact case class_document.sc's old wordninja pipeline is
        # documented to mis-split (-> [bank, account, service, i, mpl]) --
        # the regex splitter is casing-driven, not dictionary-driven, so
        # it doesn't need "impl" to be a known English word.
        self.assertEqual(
            split_identifier("BankAccountServiceImpl"),
            ["bank", "account", "service", "impl"],
        )

    def test_leading_acronym_run_kept_together(self):
        self.assertEqual(split_identifier("HTTPServer"), ["http", "server"])

    def test_acronym_in_the_middle(self):
        self.assertEqual(split_identifier("parseXMLDocument"), ["parse", "xml", "document"])

    def test_snake_case(self):
        self.assertEqual(split_identifier("MAX_ITEM_COUNT"), ["max", "item", "count"])

    def test_sentence_with_punctuation(self):
        self.assertEqual(
            split_identifier("Analyze this, not `java`."),
            ["analyze", "this", "not", "java"],
        )


class PreprocessDocumentTests(unittest.TestCase):
    def test_drops_stopwords_and_short_tokens(self):
        # "do" is an English stopword, "a" is under the length floor --
        # both should vanish, leaving only "process"/"two".
        doc = preprocess_document(["doProcessTwo", "doA"])
        self.assertEqual(doc, "process two")

    def test_synthetic_marker_backstop_drops_whole_term(self):
        doc = preprocess_document(["<operator>.assignment", "AccountService"])
        self.assertEqual(doc, "account service")

    def test_empty_terms_yield_empty_document(self):
        self.assertEqual(preprocess_document(["a", "to", "<init>"]), "")

    def test_joern_lambda_marker_is_removed(self):
        self.assertEqual(
            preprocess_document(["<lambda>0", "createAccount"]),
            "create account",
        )
        self.assertTrue(
            is_noise("org.example.AccountService.<lambda>0:void(java.lang.Object)")
        )


class EmbeddingModelCacheTests(unittest.TestCase):
    @patch("backend.src.flowmap.domain.util.SentenceTransformer")
    def test_reuses_one_model_instance_for_multiple_batches(self, model_class):
        model_class.return_value.encode.return_value = np.array([[1.0, 0.0]])
        get_embedding_model.cache_clear()
        try:
            embed_documents(["first"], model_name="test-model")
            embed_documents(["second"], model_name="test-model")
        finally:
            get_embedding_model.cache_clear()
        model_class.assert_called_once_with("test-model")


class LabelClustersStatisticalTests(unittest.TestCase):
    def test_cluster_specific_terms_outrank_shared_terms(self):
        # "account" appears in every doc across both clusters -- max_df
        # would drop it outright; c-TF-IDF's own idf weighting should
        # ALSO rank each cluster's distinctive term above whatever shared
        # vocabulary survives max_df.
        docs = [
            "account create balance",
            "account create balance",
            "account payment refund",
            "account payment refund",
        ]
        labels = [0, 0, 1, 1]
        result = label_clusters_statistical(docs, labels, max_df=1.0, top_n=3)
        self.assertIn(0, result)
        self.assertIn(1, result)
        self.assertIn("balance", result[0])
        self.assertIn("refund", result[1])
        self.assertNotIn("refund", result[0])
        self.assertNotIn("balance", result[1])

    def test_noise_label_gets_its_own_entry(self):
        result = label_clusters_statistical(["foo bar baz", "qux quux corge"], [-1, 0], max_df=1.0)
        self.assertIn(-1, result)
        self.assertIn(0, result)

    def test_single_cluster_does_not_raise_on_low_max_df(self):
        # CountVectorizer rejects max_df < 1 document as unsatisfiable
        # with only one document to count against -- a single-cluster
        # corpus (e.g. everything landed in HDBSCAN's noise bucket) must
        # still produce a label, not crash on the default max_df=0.85.
        result = label_clusters_statistical(["foo bar baz"], [-1], max_df=0.85)
        self.assertIn(-1, result)
        self.assertTrue(result[-1])


class ClusterDocumentsTests(unittest.TestCase):
    def test_single_sample_is_noise_not_an_error(self):
        # sklearn's HDBSCAN raises on n_samples < 2 unconditionally -- a
        # one-class corpus (or a single surviving doc after
        # preprocess_document drops empty ones) must not crash discover_topics.
        labels = cluster_documents(np.array([[0.1, 0.2, 0.3]]), min_cluster_size=2)
        self.assertEqual(list(labels), [-1])

    def test_empty_input_is_noise_not_an_error(self):
        labels = cluster_documents(np.empty((0, 3)), min_cluster_size=2)
        self.assertEqual(list(labels), [])


class AttachReadmeContextTests(unittest.TestCase):
    def _classes(self):
        return [
            ClassDocument("A", "com.bank.account.A", "com.bank.account", "src/A.java", []),
            ClassDocument("B", "com.bank.payment.B", "com.bank.payment", "src/B.java", []),
        ]

    def test_readme_matches_exact_package(self):
        clusters = [TopicCluster(label=0, member_full_names=["com.bank.account.A"])]
        readmes = [ReadmeDocument(path="src/README.md", package="com.bank.account", text="")]
        result = attach_readme_context(clusters, self._classes(), readmes)
        self.assertEqual(result[0].readme_paths, ["src/README.md"])

    def test_readme_matches_ancestor_package(self):
        clusters = [TopicCluster(label=0, member_full_names=["com.bank.account.A"])]
        readmes = [ReadmeDocument(path="README.md", package="com.bank", text="")]
        result = attach_readme_context(clusters, self._classes(), readmes)
        self.assertEqual(result[0].readme_paths, ["README.md"])

    def test_readme_does_not_match_unrelated_package(self):
        clusters = [TopicCluster(label=0, member_full_names=["com.bank.account.A"])]
        readmes = [ReadmeDocument(path="README.md", package="com.bank.payment", text="")]
        result = attach_readme_context(clusters, self._classes(), readmes)
        self.assertEqual(result[0].readme_paths, [])


class ExtractReadmeDocumentsTests(unittest.TestCase):
    def test_readme_mapped_to_nearest_enclosing_package(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src/com/bank/account").mkdir(parents=True)
            (root / "src/com/bank/account/README.md").write_text("account module")
            (root / "src/com/bank/payment").mkdir(parents=True)

            classes = [
                ClassDocument(
                    "AccountService",
                    "com.bank.account.AccountService",
                    "com.bank.account",
                    "src/com/bank/account/AccountService.java",
                    [],
                ),
                ClassDocument(
                    "PaymentService",
                    "com.bank.payment.PaymentService",
                    "com.bank.payment",
                    "src/com/bank/payment/PaymentService.java",
                    [],
                ),
            ]
            docs = extract_readme_documents(root, classes)

        self.assertEqual(len(docs), 1)
        self.assertEqual(docs[0].package, "com.bank.account")
        self.assertEqual(docs[0].path, "src/com/bank/account/README.md")

    def test_no_readmes_returns_empty_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            docs = extract_readme_documents(Path(tmp), [])
        self.assertEqual(docs, [])


class IsDegenerateTests(unittest.TestCase):
    def test_too_few_classes_is_degenerate_regardless_of_clusters(self):
        # Below _MIN_CLASSES_FOR_CLUSTERING -- degenerate even with a
        # clean-looking single cluster and zero noise.
        clusters = [TopicCluster(label=0, member_full_names=["a", "b", "c"])]
        self.assertTrue(is_degenerate(clusters, n_classes=5))

    def test_all_noise_is_degenerate(self):
        clusters = [TopicCluster(label=-1, member_full_names=[f"c{i}" for i in range(30)])]
        self.assertTrue(is_degenerate(clusters, n_classes=30))

    def test_high_noise_fraction_is_degenerate(self):
        clusters = [
            TopicCluster(label=-1, member_full_names=[f"n{i}" for i in range(80)]),
            TopicCluster(label=0, member_full_names=[f"c{i}" for i in range(20)]),
        ]
        self.assertTrue(is_degenerate(clusters, n_classes=100))

    def test_low_noise_fraction_is_not_degenerate(self):
        clusters = [
            TopicCluster(label=-1, member_full_names=[f"n{i}" for i in range(10)]),
            TopicCluster(label=0, member_full_names=[f"c{i}" for i in range(45)]),
            TopicCluster(label=1, member_full_names=[f"c{i}" for i in range(45, 90)]),
        ]
        self.assertFalse(is_degenerate(clusters, n_classes=100))

    def test_no_noise_cluster_present_is_not_degenerate(self):
        clusters = [
            TopicCluster(label=0, member_full_names=[f"c{i}" for i in range(50)]),
            TopicCluster(label=1, member_full_names=[f"c{i}" for i in range(50, 100)]),
        ]
        self.assertFalse(is_degenerate(clusters, n_classes=100))


class DiscoverTopicsWholeCorpusFallbackTests(unittest.TestCase):
    """
    embed_documents/cluster_documents are mocked throughout -- these tests
    are about the unconditional whole_corpus_fn fallback BRANCHING logic in
    discover_topics, not about real embedding/clustering behaviour (already
    covered elsewhere), so they stay fast/hermetic (no real model download
    or HTTP call). There is no mode switch: the fallback fires whenever
    whole_corpus_fn is supplied AND is_degenerate says so -- the only way
    to opt out is to not pass whole_corpus_fn at all.
    """

    def _class_documents(self, n, prefix="C", terms=("methodOne", "methodTwo")):
        return [
            ClassDocument(f"{prefix}{i}", f"pkg.{prefix}{i}", "pkg", f"{prefix}{i}.java", list(terms))
            for i in range(n)
        ]

    @patch("backend.src.flowmap.domain.topic_modelling.cluster_documents")
    @patch("backend.src.flowmap.domain.topic_modelling.embed_documents")
    def test_falls_back_to_whole_corpus_when_degenerate(self, mock_embed, mock_cluster):
        classes = self._class_documents(5)  # below the floor -- always degenerate
        mock_embed.return_value = np.zeros((5, 3))
        mock_cluster.return_value = np.array([0, 0, 0, 0, 0])

        whole_corpus_result = [
            TopicCluster(
                label=0, member_full_names=[c.fullName for c in classes], llm_label="Everything"
            )
        ]
        whole_corpus_fn = MagicMock(return_value=whole_corpus_result)

        result = discover_topics(classes, whole_corpus_fn=whole_corpus_fn)

        whole_corpus_fn.assert_called_once_with(classes, [])
        self.assertEqual(result[0].llm_label, "Everything")

    @patch("backend.src.flowmap.domain.topic_modelling.cluster_documents")
    @patch("backend.src.flowmap.domain.topic_modelling.embed_documents")
    def test_keeps_clustering_result_when_not_degenerate(self, mock_embed, mock_cluster):
        # Two real, distinctly-worded groups, no noise -- genuinely not
        # degenerate, so whole_corpus_fn must NOT be consulted at all even
        # though it was supplied.
        group_a = self._class_documents(20, prefix="A", terms=("accountService", "createAccount"))
        group_b = self._class_documents(5, prefix="B", terms=("paymentGateway", "processPayment"))
        classes = group_a + group_b
        mock_embed.return_value = np.zeros((25, 3))
        mock_cluster.return_value = np.array([0] * 20 + [1] * 5)

        whole_corpus_fn = MagicMock(return_value=[TopicCluster(label=0, member_full_names=[])])

        result = discover_topics(classes, whole_corpus_fn=whole_corpus_fn)

        whole_corpus_fn.assert_not_called()
        self.assertEqual({c.label for c in result}, {0, 1})

    @patch("backend.src.flowmap.domain.topic_modelling.cluster_documents")
    @patch("backend.src.flowmap.domain.topic_modelling.embed_documents")
    def test_omitting_whole_corpus_fn_keeps_clustering_result_even_if_degenerate(
        self, mock_embed, mock_cluster
    ):
        # The only opt-out: don't pass whole_corpus_fn. Even a fully
        # degenerate (all-noise) result is returned as-is, untouched.
        classes = self._class_documents(5)
        mock_embed.return_value = np.zeros((5, 3))
        mock_cluster.return_value = np.array([-1, -1, -1, -1, -1])

        result = discover_topics(classes)

        self.assertEqual(result[0].label, -1)

    @patch("backend.src.flowmap.domain.topic_modelling.cluster_documents")
    @patch("backend.src.flowmap.domain.topic_modelling.embed_documents")
    def test_label_fn_skipped_when_whole_corpus_fn_used(self, mock_embed, mock_cluster):
        classes = self._class_documents(5)
        mock_embed.return_value = np.zeros((5, 3))
        mock_cluster.return_value = np.array([0, 0, 0, 0, 0])

        whole_corpus_fn = MagicMock(return_value=[TopicCluster(label=0, member_full_names=[])])
        label_fn = MagicMock(return_value="should not be called")

        discover_topics(classes, whole_corpus_fn=whole_corpus_fn, label_fn=label_fn)

        label_fn.assert_not_called()

    @patch("backend.src.flowmap.domain.topic_modelling.cluster_documents")
    @patch("backend.src.flowmap.domain.topic_modelling.embed_documents")
    def test_centroids_reuse_the_discovery_embedding_batch(self, mock_embed, mock_cluster):
        classes = self._class_documents(4)
        mock_embed.return_value = np.array(
            [[1.0, 0.0], [1.0, 0.0], [0.0, 1.0], [0.0, 1.0]]
        )
        mock_cluster.return_value = np.array([0, 0, 1, 1])

        result = discover_topics_with_centroids(
            classes, min_cluster_size=2, max_df=1.0
        )

        mock_embed.assert_called_once()
        np.testing.assert_allclose(result.centroids[0], [1.0, 0.0])
        np.testing.assert_allclose(result.centroids[1], [0.0, 1.0])


if __name__ == "__main__":
    unittest.main()
