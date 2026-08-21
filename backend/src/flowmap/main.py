import json
import functools
import argparse
from dataclasses import dataclass
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
from domain.cfg_filtering import filter_noise_cfg
from domain.opseq_orchestration import prepare_operation_cfg
from domain.phase_orchestration import discover_phases
from service.phase import label_phase, resolve_ambiguous_phase_gates
from domain.opseq_clustering import assign_operation_topics_batch
from model import Graph


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


@dataclass(frozen=True, slots=True)
class OpseqVisualisation:
    """All graph stages for one operation, ready for export or an API response."""

    operation_cfg: Graph
    filtered_cfg: Graph
    flattened_cfg: Graph
    phase_tree: dict


def build_opseq_visualisation(
    full_cfg: Graph,
    root_id: str,
    *,
    phase_gate_resolver=None,
    phase_labeler=None,
) -> OpseqVisualisation:
    """Build the graph and phase data consumed by one operation visualisation.

    ``root_id`` is one classified opseq root. The frontend chooses among the
    complete result set, so no separate backend anchor is generated.
    """
    cfg = prepare_operation_cfg(
        full_cfg,
        root_id,
    )
    operation_cfg = cfg.sliced
    filtered_cfg = cfg.filtered
    flattened_cfg = cfg.flattened
    phase_tree = discover_phases(
        flattened_cfg,
        (node.id for node in flattened_cfg.nodes if node.type == "call"),
        gate_resolver=phase_gate_resolver,
        labeler=phase_labeler,
        timer=lambda label: timed(f"    {label}"),
    ).to_dict()
    return OpseqVisualisation(
        operation_cfg=operation_cfg,
        filtered_cfg=filtered_cfg,
        flattened_cfg=flattened_cfg,
        phase_tree=phase_tree,
    )


def build_all_opseq_visualisations(
    full_cfg: Graph, *, phase_gate_resolver=None, phase_labeler=None
) -> dict[str, dict]:
    """Precompute the frontend payload for every classified operation root."""
    payloads: dict[str, dict] = {}
    for root_id, method_full_name in root_method_full_names(full_cfg).items():
        with timed(f"Opseq visualisation: {method_full_name}"):
            visualisation = build_opseq_visualisation(
                full_cfg,
                root_id,
                phase_gate_resolver=phase_gate_resolver,
                phase_labeler=phase_labeler,
            )
        payloads[root_id] = {
            "rootMethodFullName": method_full_name,
            "memberMethodFullNames": sorted({
                node.calleeFullName
                for node in visualisation.operation_cfg.nodes
                if node.type == "entry" and node.calleeFullName is not None
            }),
            "graph": visualisation.flattened_cfg.to_dict(),
            "phaseTree": visualisation.phase_tree,
        }
    return payloads


def export_cfg_visualisations(
    full_cfg: Graph,
    output_dir: Path,
    *,
    phase_gate_resolver=None,
    phase_labeler=None,
) -> None:
    """Export the whole CFG and complete operation-visualisation collection."""
    full_cfg = filter_and_classify_roots_and_orphans(full_cfg)
    export_to_json(output_dir / "full_cfg.json", full_cfg.to_dict())
    export_to_json(
        output_dir / "opseq_visualisations.json",
        build_all_opseq_visualisations(
            full_cfg,
            phase_gate_resolver=phase_gate_resolver,
            phase_labeler=phase_labeler,
        ),
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate FlowMap analysis artifacts.")
    parser.add_argument(
        "--mode",
        choices=("cfg", "topics", "all"),
        default="all",
        help=(
            "cfg generates CFGs and resolves/labels phases; topics generates "
            "topic/opseq labels; all runs both."
        ),
    )
    parser.add_argument(
        "--provider",
        choices=PROVIDERS,
        default="groq",
        help=(
            "LLM provider for every model call in this run. groq reads "
            "GROQ_API_KEY; together reads TOGETHERAI_API_KEY."
        ),
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

    cpg_output_path = Path(OUTPUT_DIR) / "cpg.bin"
    if not cpg_output_path.exists():
        parse_project(SOURCE_DIR, cpg_output_path)
    # parse_project(SOURCE_DIR, cpg_output_path)

    session = JoernSession(port=8080)
    
    with timed("Joern session and CPG loading"):
        try:
            session.start()
            session.load_cpg(cpg_output_path)
            if args.mode in ("topics", "all"):
                class_docs, method_docs = extract_class_and_method_documents(session)
            full_cfg = extract_full_cfg(session, SOURCE_DIR)
        finally:
            session.stop()

    if args.mode in ("topics", "all"):
        # MODE 1 TOPIC CLUSTERING
        with timed("Topic clustering"):
            client = get_client(args.provider)
            readme_docs = extract_readme_documents(SOURCE_DIR, class_docs)
            class_by_full_name = {c.fullName: c for c in class_docs}

            label_fn = functools.partial(
                label_cluster, client, class_by_full_name=class_by_full_name
            )
            whole_corpus_fn = functools.partial(discover_topics_whole_corpus, client)

            topic_discovery = discover_topics_with_centroids(
                class_docs, readme_docs,
                label_fn=label_fn,
                whole_corpus_fn=whole_corpus_fn,
            )
            topic_clusters = topic_discovery.clusters

        # OPSEQ TOPIC ASSIGNMENT & LABELING
        with timed("Opseq topic assignment & labeling"):
            classify_fn = functools.partial(classify_operation, client) # assign opseq to a cluster
            opseq_label_fn = functools.partial(label_opseq, client) # label opseq with a human-readable phrase

            formed_by_llm = not any(cluster.statistical_terms for cluster in topic_clusters)
            clusters_by_label = {cluster.label: cluster for cluster in topic_clusters}

            full_cfg = filter_and_classify_roots_and_orphans(full_cfg)
            root_methods = root_method_full_names(full_cfg)

            opseqs = {
                root_id: filter_noise_cfg(slice_from_root(full_cfg, root_id))
                for root_id in full_cfg.roots
            }
            opseq_topic_assignment = assign_operation_topics_batch(
                opseqs,
                topic_clusters,
                method_docs,
                topic_discovery.centroids,
                formed_by_llm=formed_by_llm,
                classify_fn=classify_fn,
            )
            opseq_labels = {}
            for root_id, opseq in opseqs.items():
                topic_assignment = opseq_topic_assignment[root_id]
                assigned_cluster = (
                    clusters_by_label.get(topic_assignment[0].label) if topic_assignment else None
                )
                try:
                    opseq_labels[root_id] = opseq_label_fn(opseq, assigned_cluster, method_docs)
                except RuntimeError as exc:
                    print(f"label_opseq failed for {root_id!r}: {exc!r}")
                    opseq_labels[root_id] = None

        with timed("Outputting topic results"):
            topic_cluster_path = Path(OUTPUT_DIR) / "topic_cluster.json"
            export_to_json(topic_cluster_path, [topic.to_dict() for topic in topic_clusters])
            
            opseq_topic_assignment_path = Path(OUTPUT_DIR) / "opseq_topic_assignment.json"
            export_to_json(
                opseq_topic_assignment_path,
                {
                    root_id: [assignment.to_dict() for assignment in assignments]
                    for root_id, assignments in opseq_topic_assignment.items()
                },
            )
            
            opseq_labels_path = Path(OUTPUT_DIR) / "opseq_labels.json"
            export_to_json(opseq_labels_path, opseq_labels)

            topic_operations_path = Path(OUTPUT_DIR) / "topic_operations.json"
            export_to_json(
                topic_operations_path,
                combine_topic_operations(opseq_topic_assignment, opseq_labels, root_methods),
            )

    if args.mode in ("cfg", "all"):
        with timed("CFG visualisation export"):
            phase_client = client if args.mode == "all" else get_client(args.provider)
            phase_gate_resolver = functools.partial(
                resolve_ambiguous_phase_gates, phase_client
            )
            phase_labeler = functools.partial(label_phase, phase_client)
            export_cfg_visualisations(
                full_cfg,
                OUTPUT_DIR,
                phase_gate_resolver=phase_gate_resolver,
                phase_labeler=phase_labeler,
            )
