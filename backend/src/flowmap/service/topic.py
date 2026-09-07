import json
import re
import sys
from pathlib import Path

from llm.client import LLMClient
from llm.parsing import parse_json_object
from llm.parsing import correlate_records
from llm.policy import BatchQuery, Query, run_batched_items, run_query
from llm.settings import LLM_BATCH_SIZES

from joern.joern_session import JoernSession
from model import ClassDocument, MethodDocument, ReadmeDocument, TopicCluster
from llm.prompt import (
    _LABEL_RETRY_PROMPT,
    _LABEL_SYSTEM_PROMPT,
    _WHOLE_CORPUS_RETRY_PROMPT,
    _WHOLE_CORPUS_SYSTEM_PROMPT,
    cluster_label_payload,
    whole_corpus_payload,
)

_NOISE_PATTERNS = json.loads(
    (Path(__file__).parent.parent / "config" / "noise_patterns.json").read_text()
)
_CLASS_DOCUMENT_SC = (
    Path(__file__).parent.parent / "joern" / "scripts" / "class_document.sc"
).read_text()

_ANGLE_BRACKET_MARKERS = tuple(_NOISE_PATTERNS["angle_bracket_markers"])
_JDK_LEAF_OMIT_PREFIXES = tuple(_NOISE_PATTERNS["jdk_leaf_omit_prefixes"])
_SYNTHETIC = _NOISE_PATTERNS["call_site_noise"]["synthetic"]
_ACCESSOR_PREFIX = _SYNTHETIC["accessor_prefix"]
_LAMBDA_PATTERN = _SYNTHETIC["lambda_infix_regex"]
_ANON_PATTERN = _SYNTHETIC["anonymous_class_suffix_regex"]
_TOPIC_LABEL_RE = re.compile(r"^[A-Za-z]+(?: [A-Za-z]+){1,3}$")


def extract_class_and_method_documents(
    session: JoernSession,
) -> tuple[list[ClassDocument], list[MethodDocument]]:
    """
    Runs class_document.sc and returns structured class evidence alongside
    the structured per-method evidence documents.
    """
    angle_bracket_scala = ", ".join(f'"{marker}"' for marker in _ANGLE_BRACKET_MARKERS)
    jdk_scala = ", ".join(f'"{prefix}"' for prefix in _JDK_LEAF_OMIT_PREFIXES)

    script = (
        _CLASS_DOCUMENT_SC.replace("ANGLE_BRACKET_MARKERS_PLACEHOLDER", angle_bracket_scala)
        .replace("JDK_PREFIXES_PLACEHOLDER", jdk_scala)
        .replace("ACCESSOR_PREFIX_PLACEHOLDER", json.dumps(_ACCESSOR_PREFIX))
        .replace("LAMBDA_REGEX_PLACEHOLDER", json.dumps(_LAMBDA_PATTERN))
        .replace("ANON_REGEX_PLACEHOLDER", json.dumps(_ANON_PATTERN))
    )

    result = session.query_script_json(script)
    classes = [ClassDocument.from_dict(c) for c in result["classes"]]
    methods = [MethodDocument.from_dict(m) for m in result.get("methods", [])]
    return classes, methods


def extract_class_documents(session: JoernSession) -> list[ClassDocument]:
    """
    Build one structured ClassDocument per project class via class_document.sc.
    """
    return extract_class_and_method_documents(session)[0]


# Kept as private compatibility aliases while payload ownership lives in prompt.py.
_whole_corpus_prompt = whole_corpus_payload


def label_clusters(
    client: LLMClient,
    clusters: list[TopicCluster],
    class_by_full_name: dict[str, ClassDocument] | None = None,
) -> dict[int, str]:
    """Label clusters in token-bounded, independently recoverable batches."""
    def parse(raw: str, requested_ids: set[str]) -> dict[str, str]:
        def validate(record: dict) -> str | None:
            cluster_id = record.get("id")
            value = record.get("label")
            if not isinstance(value, str):
                print(
                    f"[cluster-label] id={cluster_id!r} invalid label value "
                    f"{value!r}: expected text",
                    file=sys.stderr,
                )
                return None
            if not _TOPIC_LABEL_RE.fullmatch(value):
                print(
                    f"[cluster-label] id={cluster_id!r} invalid label value "
                    f"{value!r}: expected 2-4 words containing letters only",
                    file=sys.stderr,
                )
                return None
            return value

        return correlate_records(
            raw,
            requested_ids,
            validate=validate,
            required_keys=frozenset({"id", "label"}),
            issue=lambda message: print(f"[cluster-label] {message}", file=sys.stderr),
        )

    resolved = run_batched_items(
        client,
        [cluster for cluster in clusters if cluster.label != -1],
        item_id=lambda cluster: str(cluster.label),
        build_payload=lambda batch, _resolved: cluster_label_payload(
            batch, class_by_full_name
        ),
        parse=parse,
        query=BatchQuery(
            role="small",
            initial_system=_LABEL_SYSTEM_PROMPT,
            retry_system=_LABEL_RETRY_PROMPT,
            call_site="label_clusters",
            batch_sizes=LLM_BATCH_SIZES,
        ),
    )
    return {int(cluster_id): label for cluster_id, label in resolved.items()}


def discover_topics_whole_corpus(
    client: LLMClient,
    class_documents: list[ClassDocument],
    readme_documents: list[ReadmeDocument] | None = None,
) -> list[TopicCluster]:
    """
    Ask the LLM to group all classes into thematic clusters, using at most
    three total attempts for provider failures or invalid JSON.
    """
    readme_documents = readme_documents or []
    all_full_names = {doc.fullName for doc in class_documents}

    def parse_groups(raw: str) -> dict | None:
        parsed = parse_json_object(raw)
        if parsed is None or not isinstance(parsed.get("groups"), list):
            return None
        for group in parsed["groups"]:
            if (
                not isinstance(group, dict)
                or not isinstance(group.get("label"), str)
                or not isinstance(group.get("member_full_names"), list)
                or not all(
                    isinstance(name, str) for name in group["member_full_names"]
                )
            ):
                return None
        return parsed

    issues: list[str] = []
    parsed = run_query(
        client,
        whole_corpus_payload(class_documents, readme_documents),
        parse=parse_groups,
        query=Query(
            role="large",
            initial_system=_WHOLE_CORPUS_SYSTEM_PROMPT,
            retry_system=_WHOLE_CORPUS_RETRY_PROMPT,
            call_site="discover_topics_whole_corpus",
            json_object=True,
            max_input_tokens=100_000,
            max_tokens=8_192,
        ),
        issue=issues.append,
    )
    if parsed is None:
        if any("context length" in issue.casefold() for issue in issues):
            raise RuntimeError(
                "Whole-corpus fallback is unavailable because the complete request "
                "exceeds the model context length; no classes were silently omitted"
            )
        raise RuntimeError("Whole-corpus topic discovery failed after 3 attempts")

    clusters: list[TopicCluster] = []
    assigned: set[str] = set()
    for index, group in enumerate(parsed.get("groups", [])):
        members = [
            name
            for name in group.get("member_full_names", [])
            if name in all_full_names
        ]
        assigned.update(members)
        clusters.append(
            TopicCluster(
                label=index,
                member_full_names=members,
                llm_label=group.get("label"),
                fallback=True,
            )
        )

    unassigned = sorted(all_full_names - assigned)
    if unassigned:
        clusters.append(
            TopicCluster(label=-1, member_full_names=unassigned, fallback=True)
        )

    return clusters
