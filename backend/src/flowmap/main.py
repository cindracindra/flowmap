import json
import functools
import argparse
import atexit
import hashlib
import os
import platform
import subprocess
from dataclasses import asdict
from importlib.metadata import PackageNotFoundError, version
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
from joern.util import find_joern
from service.cpg import parse_project
from service.cfg import (
    attach_targeted_data_edges,
    extract_cfg_structure,
    extract_targeted_ddg_edges,
)
from service.topic import (
    extract_class_and_method_documents,
    label_clusters,
    discover_topics_whole_corpus,
)
from service.operation_sequence import (
    classify_operation_topics,
    label_operation_sequences_with_llm,
)

from domain.topic_discovery import (
    FLOWMAP_PRESETS,
    discover_topics_with_centroids,
    extract_readme_documents,
    flowmap_config_for_preset,
    summarize_topic_coverage,
)
from domain.cfg_slicing import filter_and_classify_roots_and_orphans
from domain.operation_sequence import (
    assign_operation_sequences_to_topics,
    discover_operation_sequences,
    label_operation_sequences,
)
from domain.execution_phase import execution_phase_analysis
from domain.phase_data_flow import build_phase_data_flow_questions
from domain.method_phase_label import label_method_analysis
from domain.method_structure_validation import validate_all_method_structures
from domain.method_scoping import build_method_definitions
from service.phase import resolve_execution_phase_gate_batch
from service.method_phase_label import label_method_phases as label_method_phase_batch
from model import Graph
from presentation import build_graph_bundle, serialize_graph_bundle
from data.code_eval import EvaluationRecorder, collect_codebase_stats, collect_graph_stats


with (PROJECT_ROOT / "flowmap.config.json").open() as config_file:
    FLOWMAP_CONFIG = json.load(config_file)

DEFAULT_SOURCE_DIR = (PROJECT_ROOT / FLOWMAP_CONFIG["sourceDir"]).resolve()
DEFAULT_OUTPUT_DIR = (PROJECT_ROOT / FLOWMAP_CONFIG["outputDir"]).resolve()


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
        "--source-dir",
        type=Path,
        default=DEFAULT_SOURCE_DIR,
        help="Java source tree to analyse (default: flowmap.config.json).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Unique directory for this run's generated artifacts.",
    )
    parser.add_argument(
        "--run-id",
        help="Stable experiment run identifier (default: generated UUID).",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=11,
        help="Random seed used by stochastic local analysis (default: 11).",
    )
    parser.add_argument(
        "--joern-port",
        type=int,
        default=8080,
        help="Port for the private Joern server process (default: 8080).",
    )
    parser.add_argument(
        "--cpg-path",
        type=Path,
        help=("CPG cache path. Defaults to <output-dir>/cpg.bin; use an external "
              "path to reuse one CPG across unique run directories."),
    )
    cpg_group = parser.add_mutually_exclusive_group()
    cpg_group.add_argument(
        "--reuse-cpg",
        dest="force_cpg",
        action="store_false",
        help="Reuse --cpg-path when it exists (default).",
    )
    cpg_group.add_argument(
        "--force-cpg",
        dest="force_cpg",
        action="store_true",
        help="Re-extract cpg.bin even when a cached artifact already exists.",
    )
    parser.set_defaults(force_cpg=False)
    parser.add_argument(
        "--flowmap-preset",
        choices=tuple(FLOWMAP_PRESETS),
        default="balanced",
        help=(
            "Topic granularity: fine creates more/smaller topics, balanced "
            "uses thesis defaults, and coarse creates fewer/larger topics."
        ),
    )
    parser.add_argument(
        "--whole-corpus-topics",
        action="store_true",
        help=(
            "Force LLM whole-corpus topic grouping instead of local HDBSCAN "
            "clustering and its automatic degeneracy fallback."
        ),
    )
    parser.add_argument(
        "--eval-output",
        nargs="?",
        const="__AUTO__",
        default=None,
        metavar="PATH",
        help=(
            "Record structured stage and LLM telemetry. If PATH is omitted, "
            "write telemetry.json inside the selected output directory."
        ),
    )
    return parser.parse_args()


_ARTIFACT_NAMES = {
    "raw_cfg.json", "full_cfg.json", "graph_bundle.json", "topic_cluster.json",
    "opseq_topic_assignment.json", "opseq_labels.json", "topic_operations.json",
    "phase_gate_decisions.json",
}


def _prepare_paths(args: argparse.Namespace) -> tuple[Path, Path, Path]:
    source_dir = args.source_dir.expanduser().resolve()
    output_dir = args.output_dir.expanduser().resolve()
    cpg_path = (
        args.cpg_path.expanduser().resolve()
        if args.cpg_path is not None
        else output_dir / "cpg.bin"
    )
    if not source_dir.is_dir() or not any(source_dir.rglob("*.java")):
        raise ValueError(f"source directory contains no Java files: {source_dir}")
    existing = sorted(name for name in _ARTIFACT_NAMES if (output_dir / name).exists())
    if existing:
        raise FileExistsError(
            f"output directory already contains FlowMap artifacts: {output_dir} "
            f"({', '.join(existing)}). Use a unique --output-dir for every run."
        )
    output_dir.mkdir(parents=True, exist_ok=True)
    cpg_path.parent.mkdir(parents=True, exist_ok=True)
    return source_dir, output_dir, cpg_path


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _command_version(command: list[str]) -> str | None:
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    rendered = (result.stdout or result.stderr).strip()
    return rendered.splitlines()[0] if rendered else None


def _git_revision(path: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "HEAD"],
            check=True, capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() or None


def _git_dirty(path: Path) -> bool | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(path), "status", "--porcelain"],
            check=True, capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return bool(result.stdout.strip())


def _package_versions() -> dict[str, str | None]:
    packages = ("numpy", "scikit-learn", "sentence-transformers", "umap-learn", "openai", "groq")
    found: dict[str, str | None] = {}
    for package in packages:
        try:
            found[package] = version(package)
        except PackageNotFoundError:
            found[package] = None
    return found


def _system_memory_bytes() -> int | None:
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except (AttributeError, OSError, ValueError):
        return None


def _manifest(
    args: argparse.Namespace, source_dir: Path, output_dir: Path, cpg_path: Path
) -> dict[str, Any]:
    prompt_path = PROJECT_ROOT / "backend/src/flowmap/llm/prompt.py"
    config_payload = {
        "flowmap_preset": args.flowmap_preset,
        "whole_corpus_topics": args.whole_corpus_topics,
        "seed": args.seed,
    }
    return {
        "provider": args.provider,
        "force_cpg": args.force_cpg,
        "cpg_mode": "force" if args.force_cpg else "reuse",
        "cpg_path": str(cpg_path),
        "whole_corpus_topics": args.whole_corpus_topics,
        "flowmap_preset": args.flowmap_preset,
        "seed": args.seed,
        "source_dir": str(source_dir),
        "output_dir": str(output_dir),
        "flowmap_git_revision": _git_revision(PROJECT_ROOT),
        "flowmap_git_dirty": _git_dirty(PROJECT_ROOT),
        "subject_git_revision": _git_revision(source_dir),
        "subject_git_dirty": _git_dirty(source_dir),
        "configuration_sha256": hashlib.sha256(
            json.dumps(config_payload, sort_keys=True).encode()
        ).hexdigest(),
        "prompt_sha256": _sha256(prompt_path),
        "temperature": 0,
        "reasoning_effort": "low",
        "llm_models": {
            "small": "Qwen/Qwen3.5-9B",
            "large": "openai/gpt-oss-120b",
        },
        "joern_port": args.joern_port,
        "java_version": _command_version(["java", "-version"]),
        "joern_version": _command_version([find_joern(), "--version"]),
        "command": sys.argv,
        "machine": platform.machine(),
        "processor": platform.processor(),
        "logical_cpu_count": os.cpu_count(),
        "system_memory_bytes": _system_memory_bytes(),
        "package_versions": _package_versions(),
    }


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
    SOURCE_DIR, OUTPUT_DIR, cpg_output_path = _prepare_paths(args)
    if args.eval_output == "__AUTO__":
        args.eval_output = str(OUTPUT_DIR / "telemetry.json")
    recorder = (
        EvaluationRecorder(
            run_id=args.run_id,
            manifest=_manifest(args, SOURCE_DIR, OUTPUT_DIR, cpg_output_path),
        )
        if args.eval_output is not None else None
    )
    partial_writer = None
    if recorder is not None:
        # Preserve partial telemetry on uncaught failures (including the failed
        # stage recorded by EvaluationRecorder.stage).
        def write_partial() -> None:
            recorder.finish(success=False)
            recorder.write_json(args.eval_output)
        partial_writer = write_partial
        atexit.register(partial_writer)
    client = get_client(
        args.provider,
        telemetry_sink=recorder.record_llm_call if recorder is not None else None,
        batch_telemetry_sink=(
            recorder.record_llm_batch if recorder is not None else None
        ),
    )

    # 1. Reuse the cached CPG unless the caller explicitly invalidates it.
    with timed("CPG preparation", recorder):
        cpg_existed = cpg_output_path.exists()
        if args.force_cpg or not cpg_existed:
            parse_project(SOURCE_DIR, cpg_output_path)
        if recorder is not None:
            recorder.run.manifest["cpg_sha256"] = _sha256(cpg_output_path)
            recorder.run.manifest["cpg_rebuilt"] = args.force_cpg or not cpg_existed

    # 2–5. Keep Joern queries separate so structural CFG and DDG cost are visible.
    session = JoernSession(port=args.joern_port)
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
    topic_output_stats: dict[str, int | float | str | None] = {}
    with timed(
        "Topic clustering", recorder,
        input_stats={"classes": len(class_docs), "methods": len(method_docs)},
        output_stats=topic_output_stats,
    ):
        readme_docs = extract_readme_documents(SOURCE_DIR, class_docs)
        class_by_full_name = {document.fullName: document for document in class_docs}
        topic_discovery = discover_topics_with_centroids(
            class_docs,
            readme_docs,
            config=flowmap_config_for_preset(args.flowmap_preset),
            random_state=args.seed,
            label_fn=functools.partial(
                label_clusters, client, class_by_full_name=class_by_full_name
            ),
            whole_corpus_fn=functools.partial(discover_topics_whole_corpus, client),
            force_whole_corpus=args.whole_corpus_topics,
        )
        topic_clusters = topic_discovery.clusters
        topic_coverage = summarize_topic_coverage(class_docs, topic_clusters)
        topic_output_stats.update(
            topics=sum(cluster.label != -1 for cluster in topic_clusters),
            topic_document_classes=topic_coverage.represented_classes,
            clustered_classes=topic_coverage.clustered_classes,
            noise_classes=topic_coverage.noise_classes,
            omitted_classes=topic_coverage.omitted_classes,
            coverage=topic_coverage.coverage,
            whole_corpus_fallback=any(cluster.fallback for cluster in topic_clusters),
        )

    # Build reusable method topology once for graph-bundle export.
    with timed("Method definition construction", recorder):
        methods_by_entry_id = validate_all_method_structures(
            build_method_definitions(filtered_cfg)
        )

    # Method analysis and method labelling are independent operations.
    phase_output_stats: dict[str, int | float | str | None] = {}
    with timed(
        "Method-level phase analysis", recorder, output_stats=phase_output_stats
    ):
        def report_method_progress(completed: int, total: int) -> None:
            if completed % 100 == 0 or completed == total:
                print(
                    f"[method-phase] analysed {completed}/{total} methods",
                    flush=True,
                )

        phase_gate_resolver = functools.partial(
            resolve_execution_phase_gate_batch, client
        )
        phase_analysis = execution_phase_analysis(
            filtered_cfg,
            methods_by_entry_id,
            phase_gate_resolver,
            progress_callback=report_method_progress,
        )
        analyses = tuple(phase_analysis.analyses_by_entry_id.values())
        phase_output_stats.update(
            analysed_methods=len(analyses),
            phases=sum(len(analysis.phases) for analysis in analyses),
            phase_members=sum(
                len(phase.nodes) for analysis in analyses for phase in analysis.phases
            ),
            retained_calls=sum(len(analysis.retained_call_ids) for analysis in analyses),
            unresolved_gates=sum(len(analysis.unresolved_gates) for analysis in analyses),
            unresolved_gate_questions=len(phase_analysis.unresolved_gate_questions),
            excluded_operations=len(phase_analysis.excluded),
            llm_gate_decisions=len(phase_analysis.resolved_gate_decisions),
            llm_gate_merges=sum(
                decision.action == "MERGE"
                for decision in phase_analysis.resolved_gate_decisions
            ),
            llm_gate_splits=sum(
                decision.action == "SPLIT"
                for decision in phase_analysis.resolved_gate_decisions
            ),
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
        opseqs = discover_operation_sequences(filtered_cfg)
        operation_output_stats["operations"] = len(opseqs)
        operation_output_stats["total_nodes"] = sum(len(graph.nodes) for graph in opseqs.values())
        operation_output_stats["total_edges"] = sum(len(graph.edges) for graph in opseqs.values())

    # 11–12. Assign every operation to topics and generate its display name.
    assignment_output_stats: dict[str, int | float | str | None] = {}
    with timed(
        "Operation topic assignment and naming", recorder,
        input_stats={"operations": len(opseqs), "topics": len(topic_clusters)},
        output_stats=assignment_output_stats,
    ):
        opseq_topic_assignment = assign_operation_sequences_to_topics(
            opseqs,
            topic_clusters,
            method_docs,
            topic_discovery.centroids,
            classify_fn=functools.partial(classify_operation_topics, client),
        )
        opseq_labels = label_operation_sequences(
            opseqs,
            opseq_topic_assignment,
            topic_clusters,
            label_fn=functools.partial(
                label_operation_sequences_with_llm, client
            ),
        )
        assignment_output_stats.update(
            assigned_operations=sum(bool(items) for items in opseq_topic_assignment.values()),
            unassigned_operations=sum(not items for items in opseq_topic_assignment.values()),
            labelled_operations=sum(bool(label) for label in opseq_labels.values()),
        )

    # Build and export every artifact only after all analysis is complete.
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
        export_to_json(
            Path(OUTPUT_DIR) / "phase_gate_decisions.json",
            [
                decision.to_dict()
                for decision in phase_analysis.resolved_gate_decisions
            ],
        )

    if recorder is not None:
        recorder.run.manifest["artifact_sha256"] = {
            name: _sha256(OUTPUT_DIR / name) for name in sorted(_ARTIFACT_NAMES)
        }
        recorder.finish(success=True)
        evaluation_path = recorder.write_json(args.eval_output)
        if partial_writer is not None:
            atexit.unregister(partial_writer)
        print(f"Evaluation telemetry written to {evaluation_path}")
