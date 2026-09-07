from __future__ import annotations

import dataclasses
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np

# Repo root -- test/unit/ is two levels below it (see test_cfg.py's own
# note on this same insertion).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
sys.path.insert(
    0, str(Path(__file__).resolve().parents[2] / "backend" / "src" / "flowmap")
)

from backend.src.flowmap.domain.topic_discovery import (  # noqa: E402
    FLOWMAP_PRESETS,
    FlowMapConfig,
    attach_readme_context,
    build_class_embedding_document,
    build_class_term_document,
    calculate_ctfidf_scores,
    cluster_documents,
    discover_topics_with_centroids,
    extract_readme_documents,
    flowmap_config_for_preset,
    is_degenerate,
    extract_top_terms_by_cluster,
    reduce_embeddings,
)
from backend.src.flowmap.domain.topic_discovery import (  # noqa: E402
    embed_documents,
    get_embedding_model,
    preprocess_document,
    split_identifier,
)
from backend.src.flowmap.domain.util import is_noise  # noqa: E402
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


class BuildClassEmbeddingDocumentTests(unittest.TestCase):
    def test_builds_compact_structured_functional_evidence(self):
        doc = ClassDocument(
            className="AccountService",
            fullName="bank.AccountService",
            package="bank",
            filename="AccountService.java",
            methodNames=["transferFunds", "get_balance", "transferFunds"],
            memberNames=["accountRepository"],
            identifiers=["sourceAccount", "destination-account"],
            comments=["transfers money between customer accounts"],
            literals=["insufficient balance"],
        )

        self.assertEqual(
            build_class_embedding_document(doc),
            "\n".join(
                [
                    "Class: account service",
                    "Methods: transfer funds; get balance",
                    "Members: account repository",
                    "Identifiers: source account; destination account",
                    "Comments: transfers money between customer accounts",
                    "Messages: insufficient balance",
                ]
            ),
        )
        self.assertEqual(
            build_class_term_document(doc),
            " ".join(
                [
                    "account service",
                    "transfer funds",
                    "get balance",
                    "account repository",
                    "source account",
                    "destination account",
                    "transfers money between customer accounts",
                    "insufficient balance",
                ]
            ),
        )
        for header in (
            "class",
            "methods",
            "members",
            "identifiers",
            "comments",
            "messages",
        ):
            self.assertNotIn(header, build_class_term_document(doc).split())

    def test_omits_empty_categories_and_synthetic_values(self):
        doc = ClassDocument(
            className="<operator>.assignment",
            fullName="pkg.Empty",
            package="pkg",
            filename="Empty.java",
            methodNames=["<init>", "doA"],
            comments=["", "   "],
            literals=["Ready", "Ready"],
        )

        self.assertEqual(build_class_embedding_document(doc), "Messages: Ready")


class EmbeddingModelCacheTests(unittest.TestCase):
    @patch("backend.src.flowmap.domain.topic_discovery.embeddings.SentenceTransformer")
    def test_reuses_one_model_instance_for_multiple_batches(self, model_class):
        model_class.return_value.encode.return_value = np.array([[1.0, 0.0]])
        get_embedding_model.cache_clear()
        try:
            embed_documents(["first"], model_name="test-model")
            embed_documents(["second"], model_name="test-model")
        finally:
            get_embedding_model.cache_clear()
        model_class.assert_called_once_with("test-model")


class ExtractTopTermsByClusterTests(unittest.TestCase):
    def test_ctfidf_normalizes_counts_and_truncates_average_length(self):
        counts = np.array([[3, 1], [1, 2]])

        scores = calculate_ctfidf_scores(counts)

        # Cluster lengths are 4 and 3, so BERTopic truncates A=3.5 to A=3.
        expected_term_frequency = np.array([[3 / 4, 1 / 4], [1 / 3, 2 / 3]])
        expected_inverse_frequency = np.log(1 + 3 / np.array([4, 3]))
        np.testing.assert_allclose(
            scores,
            expected_term_frequency * expected_inverse_frequency,
        )

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
        result = extract_top_terms_by_cluster(docs, labels, max_df=1.0, top_n=3)
        self.assertIn(0, result)
        self.assertIn(1, result)
        self.assertIn("balance", result[0])
        self.assertIn("refund", result[1])
        self.assertNotIn("refund", result[0])
        self.assertNotIn("balance", result[1])

    def test_noise_label_gets_its_own_entry(self):
        result = extract_top_terms_by_cluster(
            ["foo bar baz", "qux quux corge"], [-1, 0], max_df=1.0
        )
        self.assertIn(-1, result)
        self.assertIn(0, result)

    def test_single_cluster_does_not_raise_on_low_max_df(self):
        # CountVectorizer rejects max_df < 1 document as unsatisfiable
        # with only one document to count against -- a single-cluster
        # corpus (e.g. everything landed in HDBSCAN's noise bucket) must
        # still produce a label, not crash on the default max_df=0.85.
        result = extract_top_terms_by_cluster(
            ["foo bar baz"], [-1], max_df=0.85
        )
        self.assertIn(-1, result)
        self.assertTrue(result[-1])


class ClusterDocumentsTests(unittest.TestCase):
    def test_single_sample_is_noise_not_an_error(self):
        # sklearn's HDBSCAN raises on n_samples < 2 unconditionally -- a
        # one-class corpus (or a single surviving doc after
        # preprocess_document drops empty ones) must not crash topic discovery.
        labels = cluster_documents(np.array([[0.1, 0.2, 0.3]]), min_cluster_size=2)
        self.assertEqual(list(labels), [-1])

    def test_empty_input_is_noise_not_an_error(self):
        labels = cluster_documents(np.empty((0, 3)), min_cluster_size=2)
        self.assertEqual(list(labels), [])


class FlowMapConfigurationTests(unittest.TestCase):
    def test_presets_expose_only_the_four_tuning_fields(self):
        self.assertEqual(
            dataclasses.asdict(flowmap_config_for_preset("balanced")),
            {
                "umap_n_components": 5,
                "umap_n_neighbors": 5,
                "min_cluster_size": 5,
                "min_samples": 3,
            },
        )
        self.assertEqual(FLOWMAP_PRESETS["fine"]["min_samples"], 1)
        self.assertEqual(FLOWMAP_PRESETS["coarse"]["umap_n_components"], 10)

    def test_reduction_uses_fixed_methodology_settings(self):
        embeddings = np.ones((6, 8))
        reduced = np.ones((6, 5))
        mock_umap = MagicMock()
        mock_umap.return_value.fit_transform.return_value = reduced

        with patch.dict(sys.modules, {"umap": MagicMock(UMAP=mock_umap)}):
            result = reduce_embeddings(embeddings, n_components=5, n_neighbors=5)

        mock_umap.assert_called_once_with(
            n_components=5,
            n_neighbors=5,
            min_dist=0.0,
            metric="cosine",
            random_state=11,
        )
        self.assertIs(result, reduced)

    def test_reduction_rejects_neighbors_not_smaller_than_corpus(self):
        with self.assertRaisesRegex(ValueError, "must be smaller"):
            reduce_embeddings(np.ones((5, 8)), n_components=5, n_neighbors=5)


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

    def test_duplicate_readme_content_prefers_project_level_document(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "README.md").write_text("Shared   project documentation\n")
            (root / "src/account").mkdir(parents=True)
            (root / "src/account/README.md").write_text(
                "shared project DOCUMENTATION"
            )

            docs = extract_readme_documents(root, [])

        self.assertEqual(len(docs), 1)
        self.assertEqual(docs[0].path, "README.md")
        self.assertEqual(docs[0].text, "Shared   project documentation\n")


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
    discover_topics_with_centroids, not about real embedding/clustering
    behaviour (already
    covered elsewhere), so they stay fast/hermetic (no real model download
    or HTTP call). The automatic fallback fires whenever whole_corpus_fn is
    supplied AND is_degenerate says so; force_whole_corpus bypasses that
    decision and always uses the supplied grouping function.
    """

    def _class_documents(self, n, prefix="C", methods=("methodOne", "methodTwo")):
        return [
            ClassDocument(
                f"{prefix}{i}",
                f"pkg.{prefix}{i}",
                "pkg",
                f"{prefix}{i}.java",
                methodNames=list(methods),
            )
            for i in range(n)
        ]

    @patch("backend.src.flowmap.domain.topic_discovery.clustering.cluster_documents")
    @patch("backend.src.flowmap.domain.topic_discovery.clustering.reduce_embeddings", side_effect=lambda vectors, **_: vectors)
    @patch("backend.src.flowmap.domain.topic_discovery.clustering.embed_documents")
    def test_falls_back_to_whole_corpus_when_degenerate(self, mock_embed, _mock_reduce, mock_cluster):
        classes = self._class_documents(5)  # below the floor -- always degenerate
        mock_embed.return_value = np.zeros((5, 3))
        mock_cluster.return_value = np.array([0, 0, 0, 0, 0])

        whole_corpus_result = [
            TopicCluster(
                label=0, member_full_names=[c.fullName for c in classes], llm_label="Everything"
            )
        ]
        whole_corpus_fn = MagicMock(return_value=whole_corpus_result)

        result = discover_topics_with_centroids(
            classes, whole_corpus_fn=whole_corpus_fn
        ).clusters

        whole_corpus_fn.assert_called_once_with(classes, [])
        self.assertEqual(result[0].llm_label, "Everything")
        self.assertTrue(result[0].fallback)

    @patch("backend.src.flowmap.domain.topic_discovery.clustering.cluster_documents")
    @patch("backend.src.flowmap.domain.topic_discovery.clustering.reduce_embeddings", side_effect=lambda vectors, **_: vectors)
    @patch("backend.src.flowmap.domain.topic_discovery.clustering.embed_documents")
    def test_keeps_clustering_result_when_not_degenerate(self, mock_embed, _mock_reduce, mock_cluster):
        # Two real, distinctly-worded groups, no noise -- genuinely not
        # degenerate, so whole_corpus_fn must NOT be consulted at all even
        # though it was supplied.
        group_a = self._class_documents(
            20, prefix="A", methods=("accountService", "createAccount")
        )
        group_b = self._class_documents(
            5, prefix="B", methods=("paymentGateway", "processPayment")
        )
        classes = group_a + group_b
        mock_embed.return_value = np.zeros((25, 3))
        mock_cluster.return_value = np.array([0] * 20 + [1] * 5)

        whole_corpus_fn = MagicMock(return_value=[TopicCluster(label=0, member_full_names=[])])

        result = discover_topics_with_centroids(
            classes, whole_corpus_fn=whole_corpus_fn
        ).clusters

        whole_corpus_fn.assert_not_called()
        self.assertEqual({c.label for c in result}, {0, 1})
        self.assertTrue(all(not cluster.fallback for cluster in result))

    @patch("backend.src.flowmap.domain.topic_discovery.clustering.cluster_documents")
    @patch("backend.src.flowmap.domain.topic_discovery.clustering.reduce_embeddings", side_effect=lambda vectors, **_: vectors)
    @patch("backend.src.flowmap.domain.topic_discovery.clustering.embed_documents")
    def test_force_whole_corpus_bypasses_local_clustering(self, mock_embed, mock_reduce, mock_cluster):
        classes = self._class_documents(25)
        mock_embed.return_value = np.ones((25, 2))
        whole_corpus_result = [
            TopicCluster(
                label=0,
                member_full_names=[c.fullName for c in classes],
                llm_label="Forced grouping",
            )
        ]
        whole_corpus_fn = MagicMock(return_value=whole_corpus_result)

        result = discover_topics_with_centroids(
            classes,
            whole_corpus_fn=whole_corpus_fn,
            force_whole_corpus=True,
        )

        mock_cluster.assert_not_called()
        mock_reduce.assert_not_called()
        whole_corpus_fn.assert_called_once_with(classes, [])
        self.assertEqual(result.clusters[0].llm_label, "Forced grouping")
        self.assertTrue(result.clusters[0].fallback)
        np.testing.assert_allclose(
            result.centroids[0], [2 ** -0.5, 2 ** -0.5]
        )

    @patch("backend.src.flowmap.domain.topic_discovery.clustering.embed_documents")
    def test_force_whole_corpus_requires_grouping_function(self, mock_embed):
        classes = self._class_documents(2)
        mock_embed.return_value = np.ones((2, 2))

        with self.assertRaisesRegex(ValueError, "requires a whole_corpus_fn"):
            discover_topics_with_centroids(classes, force_whole_corpus=True)

    @patch("backend.src.flowmap.domain.topic_discovery.clustering.cluster_documents")
    @patch("backend.src.flowmap.domain.topic_discovery.clustering.reduce_embeddings", side_effect=lambda vectors, **_: vectors)
    @patch("backend.src.flowmap.domain.topic_discovery.clustering.embed_documents")
    def test_omitting_whole_corpus_fn_keeps_clustering_result_even_if_degenerate(
        self, mock_embed, _mock_reduce, mock_cluster
    ):
        # The only opt-out: don't pass whole_corpus_fn. Even a fully
        # degenerate (all-noise) result is returned as-is, untouched.
        classes = self._class_documents(5)
        mock_embed.return_value = np.zeros((5, 3))
        mock_cluster.return_value = np.array([-1, -1, -1, -1, -1])

        result = discover_topics_with_centroids(classes).clusters

        self.assertEqual(result[0].label, -1)

    @patch("backend.src.flowmap.domain.topic_discovery.clustering.cluster_documents")
    @patch("backend.src.flowmap.domain.topic_discovery.clustering.reduce_embeddings", side_effect=lambda vectors, **_: vectors)
    @patch("backend.src.flowmap.domain.topic_discovery.clustering.embed_documents")
    def test_label_fn_skipped_when_whole_corpus_fn_used(self, mock_embed, _mock_reduce, mock_cluster):
        classes = self._class_documents(5)
        mock_embed.return_value = np.zeros((5, 3))
        mock_cluster.return_value = np.array([0, 0, 0, 0, 0])

        whole_corpus_fn = MagicMock(return_value=[TopicCluster(label=0, member_full_names=[])])
        label_fn = MagicMock(return_value="should not be called")

        discover_topics_with_centroids(
            classes, whole_corpus_fn=whole_corpus_fn, label_fn=label_fn
        )

        label_fn.assert_not_called()

    @patch("backend.src.flowmap.domain.topic_discovery.clustering.cluster_documents")
    @patch("backend.src.flowmap.domain.topic_discovery.clustering.reduce_embeddings", side_effect=lambda vectors, **_: vectors)
    @patch("backend.src.flowmap.domain.topic_discovery.clustering.embed_documents")
    def test_centroids_reuse_the_discovery_embedding_batch(self, mock_embed, _mock_reduce, mock_cluster):
        classes = (
            self._class_documents(2, prefix="A", methods=("accountService",))
            + self._class_documents(2, prefix="B", methods=("paymentGateway",))
        )
        mock_embed.return_value = np.array(
            [[1.0, 0.0], [1.0, 0.0], [0.0, 1.0], [0.0, 1.0]]
        )
        mock_cluster.return_value = np.array([0, 0, 1, 1])

        result = discover_topics_with_centroids(
            classes,
            config=FlowMapConfig(min_cluster_size=2, min_samples=1),
        )

        mock_embed.assert_called_once()
        np.testing.assert_allclose(result.centroids[0], [1.0, 0.0])
        np.testing.assert_allclose(result.centroids[1], [0.0, 1.0])


if __name__ == "__main__":
    unittest.main()
