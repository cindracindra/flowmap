import json
import functools
import argparse
from pathlib import Path
from typing import Any
from dotenv import load_dotenv

import time
from contextlib import contextmanager
from datetime import datetime

from llm.client import PROVIDERS, get_client
from joern.joern_session import JoernSession
from service.cpg import parse_project
from service.cfg import extract_full_cfg
from service.topic import (
    extract_class_and_method_documents,
    label_cluster,
    discover_topics_whole_corpus,
    classify_operation,
    label_opseq,
)

from domain.topic_modelling import (
    discover_topics_with_centroids,
    extract_readme_documents,
)
from domain.cfg_slicing import filter_and_classify_roots_and_orphans, slice_from_root
from domain.cfg_flattening import flatten_cfg
from domain.opseq_orchestration import has_operation_body
from domain.phase_orchestration import analyse_codebase_phases, discover_phases
from domain.method_phase_label import label_method_analysis
from domain.method_branch_routing import prepare_all_method_branch_routes
from domain.method_scoping import build_method_definitions
from service.phase import resolve_phase_gate_batch
from service.method_phase_label import label_method_phases as label_method_phase_batch
from domain.opseq_clustering import assign_operation_topics_batch
from domain.display_hierarchy import build_display_hierarchy
from model import Graph
from presentation import build_graph_bundle, serialize_graph_bundle


PROJECT_ROOT = Path(__file__).resolve().parents[3]
with (PROJECT_ROOT / "flowmap.config.json").open() as config_file:
    FLOWMAP_CONFIG = json.load(config_file)

SOURCE_DIR = (PROJECT_ROOT / FLOWMAP_CONFIG["sourceDir"]).resolve()
OUTPUT_DIR = (PROJECT_ROOT / FLOWMAP_CONFIG["outputDir"]).resolve()


def export_to_json(output_path: Path, content: Any) -> None:
    with output_path.open("w") as f:
        json.dump(content, f, indent=2)


def combine_topic_operations(
    assignments: dict[str, list],
    opseq_labels: dict[str, str | None],
    root_method_full_names: dict[str, str],
) -> dict[str, list[dict]]:
    """Create the topic drill-down, retaining unclassified opseqs as noise."""
    operations_by_topic: dict[str, list[dict]] = {}
    for operation_id, topic_assignments in assignments.items():
        for assignment in topic_assignments:
            root_method_full_name = root_method_full_names.get(operation_id)
            if root_method_full_name is None:
                raise ValueError(f"No root method found for operation {operation_id!r}")
            topic_id = str(assignment["label"] if isinstance(assignment, dict) else assignment.label)
            similarity = assignment["similarity"] if isinstance(assignment, dict) else assignment.similarity
            operations_by_topic.setdefault(topic_id, []).append(
                {
                    "id": operation_id,
                    "label": opseq_labels.get(operation_id) or operation_id,
                    "rootMethodFullName": root_method_full_name,
                    "similarity": similarity,
                }
            )

    classified_ids = {
        operation_id
        for operation_id, topic_assignments in assignments.items()
        if topic_assignments
    }
    for operation_id, root_method_full_name in root_method_full_names.items():
        if operation_id in classified_ids:
            continue
        operations_by_topic.setdefault("-1", []).append(
            {
                "id": operation_id,
                "label": opseq_labels.get(operation_id) or operation_id,
                "rootMethodFullName": root_method_full_name,
                "similarity": 0.0,
            }
        )

    for operations in operations_by_topic.values():
        operations.sort(key=lambda operation: operation["label"].lower())
    return operations_by_topic


def root_method_full_names(graph: Graph) -> dict[str, str]:
    """Map every classified operation root id to its entry method's full name."""
    entries_by_id = {node.id: node for node in graph.nodes if node.type == "entry"}
    names: dict[str, str] = {}
    for root_id in graph.roots:
        method = entries_by_id.get(root_id)
        if method is None or method.calleeFullName is None:
            raise ValueError(f"Operation root {root_id!r} has no entry method full name")
        names[root_id] = method.calleeFullName
    return names


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate FlowMap analysis artifacts.")
    parser.add_argument(
        "--provider",
        choices=PROVIDERS,
        default="groq",
        help="LLM provider used for all model calls (default: groq).",
    )
    parser.add_argument(
        "--force-cpg",
        action="store_true",
        help="Re-extract cpg.bin even when a cached artifact already exists.",
    )
    return parser.parse_args()


@contextmanager
def timed(label: str):
    start_wall = datetime.now().strftime("%H:%M:%S")
    start = time.perf_counter()
    print(f"[{start_wall}] START {label}")
    try:
        yield
    finally:
        elapsed = time.perf_counter() - start
        print(f"[{datetime.now().strftime('%H:%M:%S')}] DONE {label} ({elapsed:.2f}s)")


if __name__ == "__main__":
    load_dotenv()
    args = parse_args()
    client = get_client(args.provider)

    # 1. Reuse the cached CPG unless the caller explicitly invalidates it.
    with timed("CPG preparation"):
        cpg_output_path = Path(OUTPUT_DIR) / "cpg.bin"
        if args.force_cpg or not cpg_output_path.exists():
            parse_project(SOURCE_DIR, cpg_output_path)

    # 2–4. Joern is needed only for document and raw CFG extraction.
    session = JoernSession(port=8080)
    with timed("Joern extraction"):
        try:
            session.start()
            session.load_cpg(cpg_output_path)
            class_docs, method_docs = extract_class_and_method_documents(session)
            raw_cfg = extract_full_cfg(session, SOURCE_DIR)
        finally:
            session.stop()

    # 5. Topics describe the codebase documents and do not depend on CFG slices.
    with timed("Topic clustering"):
        readme_docs = extract_readme_documents(SOURCE_DIR, class_docs)
        class_by_full_name = {document.fullName: document for document in class_docs}
        topic_discovery = discover_topics_with_centroids(
            class_docs,
            readme_docs,
            label_fn=functools.partial(
                label_cluster, client, class_by_full_name=class_by_full_name
            ),
            whole_corpus_fn=functools.partial(discover_topics_whole_corpus, client),
        )
        topic_clusters = topic_discovery.clusters

    # 6. This is the one authoritative whole-codebase filtering pass.
    with timed("Whole-codebase CFG filtering and root classification"):
        filtered_cfg = filter_and_classify_roots_and_orphans(raw_cfg)

    # 7. Build reusable method topology once for graph-bundle export.
    with timed("Method definition construction"):
        methods_by_entry_id = prepare_all_method_branch_routes(
            build_method_definitions(filtered_cfg)
        )

    # 8–9. Method analysis and method labelling are independent operations.
    with timed("Method-level phase analysis"):
        phase_gate_resolver = functools.partial(resolve_phase_gate_batch, client)
        phase_analysis = analyse_codebase_phases(
            filtered_cfg,
            phase_gate_resolver,
            methods_by_entry_id,
        )

    with timed("Method-level phase labelling"):
        method_phase_labeler = functools.partial(label_method_phase_batch, client)
        label_method_analysis(phase_analysis, method_phase_labeler)

    # 10. A final classified root must produce an executable operation slice.
    # Slicing the already-filtered graph must not run noise filtering again.
    with timed("Operation sequence discovery"):
        root_methods = root_method_full_names(filtered_cfg)
        opseqs: dict[str, Graph] = {}
        for root_id in filtered_cfg.roots:
            opseq = slice_from_root(filtered_cfg, root_id)
            if not has_operation_body(opseq):
                raise ValueError(
                    f"classified root {root_id!r} has no executable operation body"
                )
            opseqs[root_id] = opseq

    # 11–12. Assign every operation to topics and generate its display name.
    with timed("Operation topic assignment and naming"):
        formed_by_llm = not any(
            cluster.statistical_terms for cluster in topic_clusters
        )
        opseq_topic_assignment = assign_operation_topics_batch(
            opseqs,
            topic_clusters,
            method_docs,
            topic_discovery.centroids,
            formed_by_llm=formed_by_llm,
            classify_fn=functools.partial(classify_operation, client),
        )
        clusters_by_label = {cluster.label: cluster for cluster in topic_clusters}
        opseq_labeler = functools.partial(label_opseq, client)
        opseq_labels: dict[str, str | None] = {}
        for root_id, opseq in opseqs.items():
            assignments = opseq_topic_assignment[root_id]
            assigned_cluster = (
                clusters_by_label.get(assignments[0].label)
                if assignments else None
            )
            try:
                opseq_labels[root_id] = opseq_labeler(
                    opseq, assigned_cluster, method_docs
                )
            except RuntimeError as exc:
                print(f"label_opseq failed for {root_id!r}: {exc!r}")
                opseq_labels[root_id] = None

    # 13–15. Flatten each filtered slice and deterministically propagate the
    # precomputed method-phase labels through the overlay. Non-retained
    # callees inherit their enclosing phase; retained callees remain separate.
    with timed("Operation flattening and deterministic phase overlay"):
        opseq_visualisations: dict[str, dict] = {}
        for root_id, opseq in opseqs.items():
            flattened_cfg = flatten_cfg(opseq)
            phase_tree = discover_phases(phase_analysis, flattened_cfg)
            opseq_visualisations[root_id] = {
                "rootMethodFullName": root_methods[root_id],
                "memberMethodFullNames": sorted({
                    node.calleeFullName
                    for node in opseq.nodes
                    if node.type == "entry" and node.calleeFullName is not None
                }),
                "graph": flattened_cfg.to_dict(),
                "displayHierarchy": build_display_hierarchy(flattened_cfg),
                "phaseTree": phase_tree,
            }

    # 16. Build and export every artifact only after all analysis is complete.
    with timed("Artifact construction and export"):
        graph_bundle = build_graph_bundle(
            filtered_cfg,
            phase_analysis,
            methods_by_entry_id,
        )
        export_to_json(Path(OUTPUT_DIR) / "raw_cfg.json", raw_cfg.to_dict())
        # Keep full_cfg.json as the established frontend-compatible name for
        # the authoritative filtered whole-codebase graph.
        export_to_json(Path(OUTPUT_DIR) / "full_cfg.json", filtered_cfg.to_dict())
        export_to_json(
            Path(OUTPUT_DIR) / "graph_bundle.json",
            serialize_graph_bundle(graph_bundle),
        )
        export_to_json(
            Path(OUTPUT_DIR) / "opseq_visualisations.json",
            opseq_visualisations,
        )
        export_to_json(
            Path(OUTPUT_DIR) / "topic_cluster.json",
            [topic.to_dict() for topic in topic_clusters],
        )
        export_to_json(
            Path(OUTPUT_DIR) / "opseq_topic_assignment.json",
            {
                root_id: [assignment.to_dict() for assignment in assignments]
                for root_id, assignments in opseq_topic_assignment.items()
            },
        )
        export_to_json(Path(OUTPUT_DIR) / "opseq_labels.json", opseq_labels)
        export_to_json(
            Path(OUTPUT_DIR) / "topic_operations.json",
            combine_topic_operations(
                opseq_topic_assignment,
                opseq_labels,
                root_methods,
            ),
        )
