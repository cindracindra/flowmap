import json
import functools
import argparse
import atexit
from dataclasses import asdict
from pathlib import Path
import sys
from typing import Any
from dotenv import load_dotenv

# `main.py` is commonly executed by file path. In that mode Python adds this
# file's directory, but not the repository root that owns `data.code_eval`.
# Resolve it from the file rather than depending on the caller's working dir.
PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import time
from contextlib import contextmanager
from datetime import datetime

from llm.client import PROVIDERS, get_client
from joern.joern_session import JoernSession
from service.cpg import parse_project
from service.cfg import (
    attach_targeted_data_edges,
    extract_cfg_structure,
    extract_targeted_ddg_edges,
)
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
from domain.phase_data_flow import build_phase_data_flow_questions
from domain.method_phase_label import label_method_analysis
from domain.method_branch_routing import prepare_all_method_branch_routes
from domain.method_scoping import build_method_definitions
from service.phase import resolve_phase_gate_batch
from service.method_phase_label import label_method_phases as label_method_phase_batch
from domain.opseq_clustering import assign_operation_topics_batch
from domain.display_hierarchy import build_display_hierarchy
from model import Graph
from presentation import build_graph_bundle, serialize_graph_bundle
from data.code_eval import EvaluationRecorder, collect_codebase_stats, collect_graph_stats


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
    default_eval_output = (
        PROJECT_ROOT
        / "data"
        / "code_eval"
        / "results"
        / f"run-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    )
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
    parser.add_argument(
        "--eval-output",
        nargs="?",
        const=str(default_eval_output),
        default=None,
        metavar="PATH",
        help=(
            "Record structured stage and LLM telemetry. If PATH is omitted, "
            "write a timestamped file under data/code_eval/results/."
        ),
    )
    return parser.parse_args()


@contextmanager
def timed(
    label: str,
    recorder: EvaluationRecorder | None = None,
    *,
    input_stats: dict[str, int | float | str | None] | None = None,
    output_stats: dict[str, int | float | str | None] | None = None,
):
    start_wall = datetime.now().strftime("%H:%M:%S")
    start = time.perf_counter()
    print(f"[{start_wall}] START {label}")
    try:
        if recorder is None:
            yield
        else:
            with recorder.stage(
                label, input_stats=input_stats, output_stats=output_stats
            ):
                yield
    finally:
        elapsed = time.perf_counter() - start
        print(f"[{datetime.now().strftime('%H:%M:%S')}] DONE {label} ({elapsed:.2f}s)")


if __name__ == "__main__":
    load_dotenv()
    args = parse_args()
    recorder = (
        EvaluationRecorder(manifest={
            "provider": args.provider,
            "force_cpg": args.force_cpg,
            "source_dir": str(SOURCE_DIR),
            "output_dir": str(OUTPUT_DIR),
        })
        if args.eval_output is not None else None
    )
    if recorder is not None:
        # Preserve partial telemetry on uncaught failures (including the failed
        # stage recorded by EvaluationRecorder.stage).
        atexit.register(recorder.write_json, args.eval_output)
    client = get_client(
        args.provider,
        telemetry_sink=recorder.record_llm_call if recorder is not None else None,
    )

    # 1. Reuse the cached CPG unless the caller explicitly invalidates it.
    with timed("CPG preparation", recorder):
        cpg_output_path = Path(OUTPUT_DIR) / "cpg.bin"
        if args.force_cpg or not cpg_output_path.exists():
            parse_project(SOURCE_DIR, cpg_output_path)

    # 2–5. Keep Joern queries separate so structural CFG and DDG cost are visible.
    session = JoernSession(port=8080)
    try:
        with timed("Joern startup and CPG load", recorder):
            session.start()
            session.load_cpg(cpg_output_path)

        with timed("Joern document extraction", recorder):
            class_docs, method_docs = extract_class_and_method_documents(session)

        cfg_output_stats: dict[str, int | float | str | None] = {}
        with timed(
            "Joern structural CFG extraction", recorder,
            output_stats=cfg_output_stats,
        ):
            cfg_structure = extract_cfg_structure(session, SOURCE_DIR)
            cfg_output_stats.update(asdict(collect_graph_stats(cfg_structure)))

        filtering_output_stats: dict[str, int | float | str | None] = {}
        with timed(
            "Whole-codebase CFG filtering and root classification",
            recorder,
            input_stats=asdict(collect_graph_stats(cfg_structure)),
            output_stats=filtering_output_stats,
        ):
            filtered_cfg = filter_and_classify_roots_and_orphans(cfg_structure)
            filtering_output_stats.update(asdict(collect_graph_stats(filtered_cfg)))

        candidate_output_stats: dict[str, int | float | str | None] = {}
        with timed(
            "Phase DDG candidate construction", recorder,
            output_stats=candidate_output_stats,
        ):
            ddg_questions = build_phase_data_flow_questions(filtered_cfg)
            candidate_output_stats.update(
                candidate_pairs=sum(map(len, ddg_questions.values())),
                unique_targets=len(ddg_questions),
            )

        ddg_output_stats: dict[str, int | float | str | None] = {}
        with timed(
            "Joern targeted DDG extraction", recorder,
            input_stats={
                "retained_call_nodes": filtering_output_stats.get("call_nodes"),
                "candidate_pairs": candidate_output_stats["candidate_pairs"],
                "unique_targets": candidate_output_stats["unique_targets"],
            },
            output_stats=ddg_output_stats,
        ):
            data_edges, ddg_stats = extract_targeted_ddg_edges(
                session, ddg_questions
            )
            ddg_output_stats.update({
                key: value
                for key, value in ddg_stats.items()
                if isinstance(value, (int, float, str)) or value is None
            })
            ddg_output_stats["missing_target_count"] = len(
                ddg_stats.get("missingTargets", [])
            )
            filtered_cfg = attach_targeted_data_edges(
                filtered_cfg, data_edges, ddg_questions
            )
    finally:
        session.stop()
    if recorder is not None:
        recorder.run.codebase = collect_codebase_stats(
            SOURCE_DIR,
            class_documents=class_docs,
            method_documents=method_docs,
        )

    # Topics describe the codebase documents and do not depend on CFG slices.
    with timed(
        "Topic clustering", recorder,
        input_stats={"classes": len(class_docs), "methods": len(method_docs)},
    ):
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

    # Build reusable method topology once for graph-bundle export.
    with timed("Method definition construction", recorder):
        methods_by_entry_id = prepare_all_method_branch_routes(
            build_method_definitions(filtered_cfg)
        )

    # Method analysis and method labelling are independent operations.
    with timed("Method-level phase analysis", recorder):
        phase_gate_resolver = functools.partial(resolve_phase_gate_batch, client)
        phase_analysis = analyse_codebase_phases(
            filtered_cfg,
            phase_gate_resolver,
            methods_by_entry_id,
        )

    with timed("Method-level phase labelling", recorder):
        method_phase_labeler = functools.partial(label_method_phase_batch, client)
        label_method_analysis(phase_analysis, method_phase_labeler)

    # 10. A final classified root must produce an executable operation slice.
    # Slicing the already-filtered graph must not run noise filtering again.
    operation_output_stats: dict[str, int | float | str | None] = {}
    with timed(
        "Operation sequence discovery", recorder,
        input_stats={"roots": len(filtered_cfg.roots)},
        output_stats=operation_output_stats,
    ):
        root_methods = root_method_full_names(filtered_cfg)
        opseqs: dict[str, Graph] = {}
        for root_id in filtered_cfg.roots:
            opseq = slice_from_root(filtered_cfg, root_id)
            if not has_operation_body(opseq):
                raise ValueError(
                    f"classified root {root_id!r} has no executable operation body"
                )
            opseqs[root_id] = opseq
        operation_output_stats["operations"] = len(opseqs)
        operation_output_stats["total_nodes"] = sum(len(graph.nodes) for graph in opseqs.values())
        operation_output_stats["total_edges"] = sum(len(graph.edges) for graph in opseqs.values())

    # 11–12. Assign every operation to topics and generate its display name.
    with timed(
        "Operation topic assignment and naming", recorder,
        input_stats={"operations": len(opseqs), "topics": len(topic_clusters)},
    ):
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
    with timed("Operation flattening and deterministic phase overlay", recorder):
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
    with timed("Artifact construction and export", recorder):
        graph_bundle = build_graph_bundle(
            filtered_cfg,
            phase_analysis,
            methods_by_entry_id,
        )
        export_to_json(Path(OUTPUT_DIR) / "raw_cfg.json", cfg_structure.to_dict())
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

    if recorder is not None:
        evaluation_path = recorder.write_json(args.eval_output)
        atexit.unregister(recorder.write_json)
        print(f"Evaluation telemetry written to {evaluation_path}")
