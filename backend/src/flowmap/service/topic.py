import json
import re
from pathlib import Path

from llm.client import LLMClient, LLMError

from joern.joern_session import JoernSession
from model import ClassDocument, Graph, MethodDocument, ReadmeDocument, TopicCluster

from llm.prompt import (
    _LABEL_SYSTEM_PROMPT,
    _WHOLE_CORPUS_SYSTEM_PROMPT,
    _CLASSIFY_OPERATION_SYSTEM_PROMPT,
    _LABEL_OPSEQ_SYSTEM_PROMPT,
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


_MAX_METHOD_PROMPT_TERMS = 20
_MAX_METHOD_PROMPT_CHARS = 1200
_MAX_PROMPT_VALUE_CHARS = 160

# Separate category budgets prevent a long method list from crowding every
# other kind of class evidence out of the prompt. Together these character
# caps also put a real upper bound of 2,000 value characters on each
# class's evidence block.
_CLASS_PROMPT_CATEGORIES = (
    ("Annotations", "annotations", 3, 240),
    ("Inherits", "inherits", 3, 240),
    ("Methods", "methodNames", 8, 400),
    ("Members", "memberNames", 4, 240),
    ("Identifiers", "identifiers", 8, 400),
    ("Comments", "comments", 2, 240),
    ("String literals", "literals", 3, 240),
)
_LEGACY_CLASS_TERMS_LIMIT = 20
_LEGACY_CLASS_CHARS_LIMIT = 800

_JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def _bounded_prompt_values(
    values: list[str], *, item_limit: int, char_limit: int
) -> str:
    """Deduplicate values in source order and enforce prompt-size bounds."""
    selected: list[str] = []
    seen: set[str] = set()
    used = 0
    for raw_value in values:
        value = " ".join(str(raw_value).split())
        if not value or value in seen:
            continue
        seen.add(value)
        if len(value) > _MAX_PROMPT_VALUE_CHARS:
            value = value[: _MAX_PROMPT_VALUE_CHARS - 1].rstrip() + "…"
        separator_length = 2 if selected else 0
        remaining = char_limit - used - separator_length
        if remaining <= 0:
            break
        if len(value) > remaining:
            if remaining < 2:
                break
            value = value[: remaining - 1].rstrip() + "…"
        selected.append(value)
        used += separator_length + len(value)
        if len(selected) >= item_limit or used >= char_limit:
            break
    return ", ".join(selected)


def _class_evidence_lines(doc: ClassDocument, *, indent: str = "") -> list[str]:
    """Balanced, category-aware evidence for one class prompt block."""
    has_structured_evidence = any(
        getattr(doc, attribute)
        for _, attribute, _, _ in _CLASS_PROMPT_CATEGORIES
    )
    lines: list[str] = []
    if has_structured_evidence:
        for label, attribute, item_limit, char_limit in _CLASS_PROMPT_CATEGORIES:
            rendered = _bounded_prompt_values(
                getattr(doc, attribute),
                item_limit=item_limit,
                char_limit=char_limit,
            )
            if rendered:
                lines.append(f"{indent}{label}: {rendered}")
        return lines

    # Compatibility for callers constructing the old flat document shape.
    rendered = _bounded_prompt_values(
        doc.terms[1:],
        item_limit=_LEGACY_CLASS_TERMS_LIMIT,
        char_limit=_LEGACY_CLASS_CHARS_LIMIT,
    )
    return [f"{indent}Evidence: {rendered}"] if rendered else []


def extract_class_and_method_documents(
    session: JoernSession,
) -> tuple[list[ClassDocument], list[MethodDocument]]:
    """
    Runs class_document.sc and returns structured class evidence alongside
    the per-method term-bag documents.
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


def _fallback_label(cluster: TopicCluster) -> str:
    return cluster.statistical_terms[0] if cluster.statistical_terms else "unlabeled"


def _cluster_prompt(
    cluster: TopicCluster, class_by_full_name: dict[str, ClassDocument] | None
) -> str:
    lines = [
        f"Top terms: {', '.join(cluster.statistical_terms)}",
        f"Classes: {', '.join(cluster.member_full_names)}",
    ]
    if class_by_full_name is not None:
        for full_name in cluster.member_full_names:
            doc = class_by_full_name.get(full_name)
            if doc is None:
                continue
            evidence = _class_evidence_lines(doc, indent="    ")
            if evidence:
                lines.append(f"  {doc.className}:")
                lines.extend(evidence)
    return "\n".join(lines)


def label_cluster(
    client: LLMClient,
    cluster: TopicCluster,
    class_by_full_name: dict[str, ClassDocument] | None = None,
) -> str:
    """
    LLM-backed cluster labeling: one call per cluster (from HDBSCAN) to 
    get a short, human-readable label for the cluster. Falls back to top
    statistical-term label on any provider failure or an empty response.
    """
    try:
        label = client.complete(
            role="small",
            system=_LABEL_SYSTEM_PROMPT,
            user=_cluster_prompt(cluster, class_by_full_name),
            max_tokens=128,
        ).strip()
        return label or _fallback_label(cluster)
    except LLMError:
        return _fallback_label(cluster)


def _whole_corpus_prompt(
    class_documents: list[ClassDocument], readme_documents: list[ReadmeDocument]
) -> str:
    lines: list[str] = []
    for doc in class_documents:
        lines.append(f"Class: {doc.fullName}")
        lines.extend(_class_evidence_lines(doc, indent="  "))
    if readme_documents:
        lines.append("")
        lines.append("Project documentation:")
        for readme in readme_documents:
            lines.append(f"[{readme.package or 'root'}] {readme.text[:500]}")
    return "\n".join(lines)


def discover_topics_whole_corpus(
    client: LLMClient,
    class_documents: list[ClassDocument],
    readme_documents: list[ReadmeDocument] | None = None,
) -> list[TopicCluster]:
    """
    LLM implementation of the whole-corpus topic discovery: one call to 
    the LLM to group all classes into thematic clusters.
    """
    readme_documents = readme_documents or []
    all_full_names = {doc.fullName for doc in class_documents}

    try:
        content = client.complete(
            role="large",
            system=_WHOLE_CORPUS_SYSTEM_PROMPT,
            user=_whole_corpus_prompt(class_documents, readme_documents),
            json_object=True,
        )
    except LLMError as e:
        raise RuntimeError(
            f"API call failed during whole-corpus topic discovery: {e}. "
            f"Check that the {client.provider} API key is set correctly and "
            "that the provider is reachable."
        ) from e

    try:
        parsed = json.loads(_JSON_FENCE_RE.sub("", content).strip())
    except json.JSONDecodeError as e:
        raise RuntimeError(
            "The model responded, but its output wasn't valid JSON during whole-corpus "
            f"topic discovery: {e}. Raw response: {content[:500]!r}"
        ) from e

    clusters: list[TopicCluster] = []
    assigned: set[str] = set()
    for index, group in enumerate(parsed.get("groups", [])):
        members = [
            name for name in group.get("member_full_names", []) if name in all_full_names
        ]
        assigned.update(members)
        clusters.append(
            TopicCluster(label=index, member_full_names=members, llm_label=group.get("label"))
        )

    unassigned = sorted(all_full_names - assigned)
    if unassigned:
        clusters.append(TopicCluster(label=-1, member_full_names=unassigned))

    return clusters


def _cluster_line(cluster: TopicCluster) -> str:
    """
    One display line for a cluster -- reused directly by label_opseq (for
    the single cluster an opseq was already assigned to) and by
    _clusters_prompt below (looped over every candidate cluster), so both
    render a cluster identically. Label falls back from llm_label to the
    cluster's own top statistical term to "(unlabeled)" -- same chain
    _fallback_label uses for a failed label_cluster call.
    """
    first_term = cluster.statistical_terms[0] if cluster.statistical_terms else None
    label_text = cluster.llm_label or first_term or "(unlabeled)"
    return f"[{cluster.label}] {label_text}: {', '.join(cluster.member_full_names)}"


def _clusters_prompt(clusters: list[TopicCluster]) -> str:
    return "\n".join(_cluster_line(cluster) for cluster in clusters)


def _operation_prompt(operation_cfg: Graph, method_documents: list[MethodDocument]) -> str:
    method_by_full_name = {m.fullName: m for m in method_documents}
    lines = []
    seen_methods: set[str] = set()
    for node in operation_cfg.nodes:
        if node.type != "entry" or not node.calleeFullName:
            continue
        if node.calleeFullName in seen_methods:
            continue
        seen_methods.add(node.calleeFullName)
        doc = method_by_full_name.get(node.calleeFullName)
        preview = (
            _bounded_prompt_values(
                doc.terms[1:],
                item_limit=_MAX_METHOD_PROMPT_TERMS,
                char_limit=_MAX_METHOD_PROMPT_CHARS,
            )
            if doc
            else ""
        )
        lines.append(
            f"{node.calleeFullName}: {preview}" if preview else node.calleeFullName
        )
    return "\n".join(lines)


def classify_operation(
    client: LLMClient,
    operation_cfg: Graph,
    clusters: list[TopicCluster],
    method_documents: list[MethodDocument],
) -> int | None:
    """
    LLM-backed topic assignment, for use when `clusters` came from LLM
    inference (discover_topics_whole_corpus), not local HDBSCAN.

    Uses response_format={"type": "json_object"} (same mechanism
    discover_topics_whole_corpus already relies on) rather than a bare
    "respond with just the integer" prompt instruction -- confirmed live
    that prompt wording alone isn't enough: a classification-shaped ask
    ("which of these fits?") invites the model to justify its answer with
    explanatory prose far more often than a generation-shaped ask ("write
    a short label") does, even at temperature=0 with an explicit "no
    explanation" instruction and a tight max_tokens. JSON mode is a real
    API-level constraint, not just a request, so it closes that failure
    mode structurally instead of hoping the wording is persuasive enough.

    Prints one diagnostic line per call, tagged with the opseq's root
    method (operation_cfg.entryPoint) -- every exit path (no candidates,
    empty response, invalid JSON, null group_id, an id outside the
    candidate list, or a genuine assignment) is distinguishable in the
    log, since they'd otherwise all collapse into an indistinguishable
    `None` return.
    """
    operation_id = operation_cfg.entryPoint or "<unknown>"

    candidates = [cluster for cluster in clusters if cluster.label != -1]
    if not candidates:
        print(f"classify_operation[{operation_id}]: no candidate clusters -> None")
        return None
    valid_labels = {cluster.label for cluster in candidates}
    try:
        raw_text = client.complete(
            role="small",
            system=_CLASSIFY_OPERATION_SYSTEM_PROMPT,
            user=(
                f"Groups:\n{_clusters_prompt(candidates)}\n\n"
                f"Operation:\n{_operation_prompt(operation_cfg, method_documents)}"
            ),
            max_tokens=128,
            json_object=True,
        )
    except LLMError as exc:
        raise RuntimeError(f"classify_operation: {exc}") from exc

    text = raw_text.strip()

    if not text:
        print(f"classify_operation[{operation_id}]: empty response -> None")
        return None
    try:
        parsed = json.loads(_JSON_FENCE_RE.sub("", text).strip())
    except json.JSONDecodeError:
        print(f"classify_operation[{operation_id}]: response wasn't valid JSON {text!r} -> None")
        return None

    group_id = parsed.get("group_id") if isinstance(parsed, dict) else None
    if group_id is None:
        print(f"classify_operation[{operation_id}]: model said no group fits -> None")
        return None
    if not isinstance(group_id, int) or group_id not in valid_labels:
        print(
            f"classify_operation[{operation_id}]: group_id {group_id!r} not a valid "
            f"label {sorted(valid_labels)} -> None"
        )
        return None

    # print(f"classify_operation[{operation_id}]: assigned label {group_id}")
    return group_id


def label_opseq(
    client: LLMClient,
    operation_cfg: Graph,
    cluster: TopicCluster | None,
    method_documents: list[MethodDocument],
) -> str | None:
    """
    LLM-backed label for one opseq -- a short, specific phrase for what
    THIS operation does (e.g. "Fund Transfer").
    Returns None on an empty response -- not a failure, just nothing
    usable to report.
    """
    content = f"Operation:\n{_operation_prompt(operation_cfg, method_documents)}"
    if cluster is not None:
        content = f"Cluster:\n{_cluster_line(cluster)}\n\n{content}"

    try:
        text = client.complete(
            role="small",
            system=_LABEL_OPSEQ_SYSTEM_PROMPT,
            user=content,
            max_tokens=128,
        ).strip()
    except LLMError as exc:
        raise RuntimeError(f"label_opseq: {exc}") from exc

    return text or None
