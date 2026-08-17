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

from llm.groq_client import get_client
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

from domain.cfg_pipeline import (
    filter_and_classify_roots_and_orphans,
    slice_from_root,
    slice_anchored_cfg,
    filter_noise_cfg,
    flatten_cfg,
)
from domain.topic_modelling import (
    discover_topics, 
    extract_readme_documents,
)
from domain.phase_discovery import build_phase_tree
from domain.opseq_clustering import assign_operation_topics
from model import Graph


PROJECT_ROOT = Path(__file__).resolve().parents[3]
with (PROJECT_ROOT / "flowmap.config.json").open() as config_file:
    FLOWMAP_CONFIG = json.load(config_file)

SOURCE_DIR = (PROJECT_ROOT / FLOWMAP_CONFIG["sourceDir"]).resolve()
OUTPUT_DIR = (PROJECT_ROOT / FLOWMAP_CONFIG["outputDir"]).resolve()
ANCHOR_NAME = FLOWMAP_CONFIG["anchorName"]


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

    anchored_cfg: Graph
    filtered_cfg: Graph
    flattened_cfg: Graph
    phase_tree: dict


def build_opseq_visualisation(full_cfg: Graph, anchor_name: str) -> OpseqVisualisation:
    """Build the graph and phase data consumed by one operation visualisation.

    `anchor_name` is the full method name attached to an opseq root. Opseqs
    are roots by definition, so exactly one anchored graph is expected. This
    function is deliberately independent of Joern/session setup: a future API
    can load the CPG once, retain ``full_cfg``, and call this per request.
    """
    anchored_cfgs = slice_anchored_cfg(full_cfg, anchor_name)
    if len(anchored_cfgs) != 1:
        raise ValueError(
            f"Expected one operation graph for {anchor_name!r}, got {len(anchored_cfgs)}"
        )

    anchored_cfg = anchored_cfgs[0]
    filtered_cfg = filter_noise_cfg(anchored_cfg)
    flattened_cfg = flatten_cfg(filtered_cfg)
    return OpseqVisualisation(
        anchored_cfg=anchored_cfg,
        filtered_cfg=filtered_cfg,
        flattened_cfg=flattened_cfg,
        phase_tree=build_phase_tree(flattened_cfg),
    )


def build_all_opseq_visualisations(full_cfg: Graph) -> dict[str, dict]:
    """Precompute the frontend payload for every classified operation root."""
    payloads: dict[str, dict] = {}
    for root_id, method_full_name in root_method_full_names(full_cfg).items():
        visualisation = build_opseq_visualisation(full_cfg, method_full_name)
        payloads[root_id] = {
            "rootMethodFullName": method_full_name,
            "memberMethodFullNames": sorted({
                node.calleeFullName
                for node in visualisation.anchored_cfg.nodes
                if node.type == "entry" and node.calleeFullName is not None
            }),
            "graph": visualisation.flattened_cfg.to_dict(),
            "phaseTree": visualisation.phase_tree,
        }
    return payloads


def export_cfg_visualisations(full_cfg: Graph, output_dir: Path, anchor_name: str) -> None:
    """Export all non-LLM graph artifacts, including every operation graph."""
    full_cfg = filter_and_classify_roots_and_orphans(full_cfg)
    export_to_json(output_dir / "full_cfg.json", full_cfg.to_dict())
    export_to_json(output_dir / "opseq_visualisations.json", build_all_opseq_visualisations(full_cfg))

    if not anchor_name.strip():
        # An empty anchor means the app has no global trace selected. Keep
        # operation visualisations available, but make the top-level graph
        # intentionally empty rather than choosing an arbitrary root.
        export_to_json(output_dir / "anchored_cfg.json", [])
        export_to_json(output_dir / "filtered_cfg.json", [])
        export_to_json(output_dir / "flattened_cfg.json", Graph().to_dict())
        export_to_json(output_dir / "phase_tree.json", {"entryPoint": "", "phases": []})
        return

    anchored_operation = build_opseq_visualisation(full_cfg, anchor_name)
    export_to_json(output_dir / "anchored_cfg.json", [anchored_operation.anchored_cfg.to_dict()])
    export_to_json(output_dir / "filtered_cfg.json", [anchored_operation.filtered_cfg.to_dict()])
    export_to_json(output_dir / "flattened_cfg.json", anchored_operation.flattened_cfg.to_dict())
    export_to_json(output_dir / "phase_tree.json", anchored_operation.phase_tree)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate FlowMap analysis artifacts.")
    parser.add_argument(
        "--mode",
        choices=("cfg", "topics", "all"),
        default="all",
        help="cfg avoids all LLM calls; topics generates topic/opseq labels; all runs both.",
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
    # if not cpg_output_path.exists():
    #     parse_project(SOURCE_DIR, cpg_output_path)
    parse_project(SOURCE_DIR, cpg_output_path)

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
            client = get_client()
            readme_docs = extract_readme_documents(SOURCE_DIR, class_docs)
            class_by_full_name = {c.fullName: c for c in class_docs}

            label_fn = functools.partial(
                label_cluster, client, class_by_full_name=class_by_full_name
            )
            whole_corpus_fn = functools.partial(discover_topics_whole_corpus, client)

            topic_clusters = discover_topics(
                class_docs, readme_docs,
                label_fn=label_fn,
                whole_corpus_fn=whole_corpus_fn,
            )

        # OPSEQ TOPIC ASSIGNMENT & LABELING
        with timed("Opseq topic assignment & labeling"):
            classify_fn = functools.partial(classify_operation, client) # assign opseq to a cluster
            opseq_label_fn = functools.partial(label_opseq, client) # label opseq with a human-readable phrase

            formed_by_llm = not any(cluster.statistical_terms for cluster in topic_clusters)
            clusters_by_label = {cluster.label: cluster for cluster in topic_clusters}

            full_cfg = filter_and_classify_roots_and_orphans(full_cfg)
            root_methods = root_method_full_names(full_cfg)

            opseq_topic_assignment = {}
            opseq_labels = {}
            for root_id in full_cfg.roots:
                opseq = filter_noise_cfg(slice_from_root(full_cfg, root_id))

                topic_assignment = assign_operation_topics(
                    opseq, topic_clusters, class_docs, method_docs,
                    formed_by_llm=formed_by_llm,
                    classify_fn=classify_fn,
                )
                opseq_topic_assignment[root_id] = topic_assignment

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
            export_cfg_visualisations(full_cfg, OUTPUT_DIR, ANCHOR_NAME)
