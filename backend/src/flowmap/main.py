import json
import functools
from pathlib import Path
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
    classify_roots_and_orphans,
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


SOURCE_DIR = Path("/Users/cindra/Documents/ImperialCollege/Thesis/test_code/java_project/java_project")
OUTPUT_DIR = Path("/Users/cindra/Documents/ImperialCollege/Thesis/test_code/java_project/output")
ANCHOR_NAME = "com.example.cpgtest.Main.runTransfer:void(com.example.cpgtest.service.AccountService,int,int)"

def export_to_json(output_path:Path, content: dict):
    with output_path.open("w") as f:
        json.dump(content, f, indent=2)


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

    cpg_output_path = Path(OUTPUT_DIR) / "cpg.bin"
    if not cpg_output_path.exists():
        parse_project(SOURCE_DIR, cpg_output_path)
    # parse_project(SOURCE_DIR, cpg_output_path)

    session = JoernSession(port=8080)
    
    with timed("Joern session and CPG loading"):
        try:
            session.start()
            session.load_cpg(cpg_output_path)
            class_docs, method_docs = extract_class_and_method_documents(session)
            full_cfg = extract_full_cfg(session)
        finally:
            session.stop()

    # # MODE 1 TOPIC CLUSTERING
    # with timed("Topic clustering"):
    #     client = get_client()
    #     readme_docs = extract_readme_documents(SOURCE_DIR, class_docs)
    #     class_by_full_name = {c.fullName: c for c in class_docs}

    #     label_fn = functools.partial(
    #         label_cluster, client, class_by_full_name=class_by_full_name
    #     )
    #     whole_corpus_fn = functools.partial(discover_topics_whole_corpus, client)

    #     topic_clusters = discover_topics(
    #         class_docs, readme_docs,
    #         label_fn=label_fn,
    #         whole_corpus_fn=whole_corpus_fn,
    #     )

    # # OPSEQ TOPIC ASSIGNMENT & LABELING
    # with timed("Opseq topic assignment & labeling"):
    #     classify_fn = functools.partial(classify_operation, client) # assign opseq to a cluster
    #     opseq_label_fn = functools.partial(label_opseq, client) # label opseq with a human-readable phrase

    #     formed_by_llm = not any(cluster.statistical_terms for cluster in topic_clusters)
    #     clusters_by_label = {cluster.label: cluster for cluster in topic_clusters}

    #     full_cfg = classify_roots_and_orphans(full_cfg)

    #     opseq_topic_assignment = {}
    #     opseq_labels = {}
    #     for root_id in full_cfg.roots:
    #         opseq = filter_noise_cfg(slice_from_root(full_cfg, root_id))

    #         topic_assignment = assign_operation_topics(
    #             opseq, topic_clusters, class_docs, method_docs,
    #             formed_by_llm=formed_by_llm,
    #             classify_fn=classify_fn,
    #         )
    #         opseq_topic_assignment[root_id] = topic_assignment

    #         assigned_cluster = (
    #             clusters_by_label.get(topic_assignment[0].label) if topic_assignment else None
    #         )
    #         try:
    #             opseq_labels[root_id] = opseq_label_fn(opseq, assigned_cluster, method_docs)
    #         except RuntimeError as exc:
    #             print(f"label_opseq failed for {root_id!r}: {exc!r}")
    #             opseq_labels[root_id] = None

    # with timed("Outputting results"):
    #     topic_cluster_path = Path(OUTPUT_DIR) / "topic_cluster.json"
    #     export_to_json(topic_cluster_path, [topic.to_dict() for topic in topic_clusters])
        
    #     opseq_topic_assignment_path = Path(OUTPUT_DIR) / "opseq_topic_assignment.json"
    #     export_to_json(
    #         opseq_topic_assignment_path,
    #         {
    #             root_id: [assignment.to_dict() for assignment in assignments]
    #             for root_id, assignments in opseq_topic_assignment.items()
    #         },
    #     )
        
    #     opseq_labels_path = Path(OUTPUT_DIR) / "opseq_labels.json"
    #     export_to_json(opseq_labels_path, opseq_labels)

    # CFG PROCESSING
    anchored_cfg = slice_anchored_cfg(full_cfg, ANCHOR_NAME)
    filtered_cfg = [filter_noise_cfg(cfg) for cfg in anchored_cfg]
    flattened_cfg = [flatten_cfg(cfg) for cfg in filtered_cfg]
    print("CFG PROCESSING DONE")

    # PHASE DISCOVERY
    phase_tree = [build_phase_tree(cfg) for cfg in flattened_cfg]
    print("PHASE DISCOVERY DONE")

    ### OUTPUT
    # topic_labels_path = Path(OUTPUT_DIR) / "topic_labels.json"
    # export_to_json(topic_labels_path, [topic.to_dict() for topic in topic_clusters])

    full_cfg_path = Path(OUTPUT_DIR) / "full_cfg.json"
    export_to_json(full_cfg_path, full_cfg.to_dict())

    anchored_cfg_path = Path(OUTPUT_DIR) / "anchored_cfg.json"
    export_to_json(anchored_cfg_path, [cfg.to_dict() for cfg in anchored_cfg])

    filtered_cfg_path = Path(OUTPUT_DIR) / "filtered_cfg.json"
    export_to_json(filtered_cfg_path, [cfg.to_dict() for cfg in filtered_cfg])

    flattened_cfg_path = Path(OUTPUT_DIR) / "flattened_cfg.json"
    export_to_json(flattened_cfg_path, [cfg.to_dict() for cfg in flattened_cfg])

    phase_tree_path = Path(OUTPUT_DIR) / "phase_tree.json"
    export_to_json(phase_tree_path, phase_tree)
