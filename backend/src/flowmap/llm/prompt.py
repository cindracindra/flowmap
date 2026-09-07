_LABEL_SYSTEM_PROMPT = (
    "Assign a concise 2-4 word functional label to EVERY supplied cluster of "
    "Java classes. Treat representativeTerms as the primary description of a "
    "cluster and use member class and method names as supporting evidence. "
    "Describe the shared user- or domain-facing responsibility. Each cluster is "
    "an independent question. HARD OUTPUT CONTRACT: return JSON Lines with one "
    "complete object per cluster and no surrounding array, Markdown, explanation, "
    "blank lines, or additional keys. Each non-empty line must match exactly: "
    '{"id":"exact-cluster-id","label":"2-4 word label"}. The label must '
    "contain letters and single spaces only. Return every supplied ID exactly "
    "once and do not invent IDs."
)
_LABEL_RETRY_PROMPT = (
    _LABEL_SYSTEM_PROMPT
    + " This corrective request contains only clusters whose previous records "
      "were missing or invalid. Return one valid JSON object line per ID."
)

_WHOLE_CORPUS_SYSTEM_PROMPT = (
    "You are analyzing a Java codebase to group its classes into "
    "thematic FEATURE groups -- what the application actually DOES for "
    "its users/domain (e.g. 'Account Management', 'Payment Processing'), "
    "not how it's built. You are given each class's fully-qualified name "
    "and representative terms from its "
    "methods, members, identifiers, comments, and string literals. Project "
    "documentation is supplied as additional codebase-level context.\n\n"
    "Do NOT create a group for, and do NOT include in any group, any class "
    "or pseudo-class that does not independently represent user/domain "
    "functionality:\n"
    "- an application entry point / bootstrap class (e.g. Main, "
    "Application, or any class whose only role is wiring/starting the app)\n"
    "- a custom exception class (its role is error signaling, not a "
    "feature in itself)\n"
    "- a generic, domain-agnostic utility/helper class with no "
    "business-specific behavior (e.g. math helpers, string helpers, "
    "logging wrappers)\n"
    "- a compiler-generated or synthetic artifact, such as lambda "
    "implementation method or pseudo-type (e.g. names containing '<lambda>' "
    "or 'lambda$'). A lambda is an implementation detail of its enclosing "
    "class, not a standalone feature. Omit it even when its terms appear "
    "business-specific\n"
    "Simply omit these classes from every group -- do not invent an "
    "'Infrastructure' or 'Utilities' catch-all group for them either; "
    "leaving a class out of every group is the correct way to exclude it.\n\n"
    "Group the remaining, genuinely feature-bearing classes by shared "
    "purpose; a class that doesn't clearly belong with others may still "
    "be its own group if it represents a real distinct feature. HARD OUTPUT "
    "CONTRACT: the complete response must be exactly one valid JSON object with "
    "no Markdown, commentary, or additional keys. It must match this shape: "
    '{"groups": [{"label": "short label", '
    '"member_full_names": ["fully.qualified.Name", ...]}]}'
)
_WHOLE_CORPUS_RETRY_PROMPT = (
    _WHOLE_CORPUS_SYSTEM_PROMPT
    + " The previous response was invalid. Return the complete corrected JSON object."
)

_CLASSIFY_OPERATION_SYSTEM_PROMPT = (
    "Assign EVERY supplied operation -- each a call chain through a Java "
    "codebase, rooted at one entry point -- to the closest-matching "
    "feature topic from an existing list. The JSON input contains topics "
    "with a stable id, label, representativeTerms, and memberClasses shape, "
    "plus each operation's initiating entryPoint and subsequentMethods, both "
    "using compact Class.method names. Treat entryPoint as the primary "
    "indication of initiating intent and use subsequentMethods to identify "
    "the concrete action, domain object, channel, or outcome. "
    "Use the topic label and member classes as primary evidence when "
    "representativeTerms is empty.\n\n"
    "If the operation genuinely doesn't fit any group -- e.g. it is purely "
    "infrastructure/bootstrap/logging with no real feature behavior -- "
    "use null instead of forcing a match.\n\n"
    "HARD OUTPUT CONTRACT: return JSON Lines with exactly one complete object "
    "per operation and no surrounding array, Markdown, commentary, blank lines, "
    "or additional keys. Each non-empty line must match exactly: "
    '{"id":"exact-operation-id","group_id":<integer topic id or null>}. '
    "Return every supplied ID exactly once and do not invent IDs."
)
_CLASSIFY_OPERATION_RETRY_PROMPT = (
    _CLASSIFY_OPERATION_SYSTEM_PROMPT
    + " This corrective request contains only operations whose previous records "
      "were missing or invalid. Return one valid JSON object line per ID."
)

_LABEL_OPSEQ_BATCH_SYSTEM_PROMPT = (
    "You are naming EVERY specific operation assigned to ONE feature topic. "
    "Each operation is a single call chain through a Java codebase, rooted at "
    "one entry point. Give every supplied operation ID a short 2-6 word "
    "operational label (e.g. 'Fund Transfer', 'Password Reset', 'Order Checkout "
    "via API', 'Create Bank Object').\n\n"
    "Every operation contains its initiating entryPoint and an ordered "
    "subsequentMethods list, both using compact Class.method names. Treat the "
    "entryPoint as the primary indication of the operation's initiating intent. "
    "Use subsequentMethods, in their supplied order, to refine the label with "
    "the concrete action, domain object, channel, or outcome. Do not rely only "
    "on a generic entry point when subsequent methods provide more specific "
    "evidence. Shared subsequent methods give topic context but must not, by "
    "themselves, be used to distinguish labels. You may also be given the "
    "broader feature topic that all operations "
    "in this request were assigned to. It uses the same id, label, "
    "representativeTerms, and memberClasses shape as operation-to-topic "
    "assignment. Treat the entry point and subsequent methods as the primary "
    "evidence for "
    "what each operation actually does.\n\n"
    "If a topic IS given: its label names the broader feature area. Every "
    "operation label must be MORE SPECIFIC than the topic label and must never "
    "merely repeat or rephrase it. If NO topic is given: name each operation "
    "solely from its own method evidence.\n\n"
    "Labels must be unique within this request, compared case-insensitively. "
    "When operations are similar, distinguish them using an evidenced difference "
    "in their concrete action, object, channel, direction, or outcome. Never "
    "invent an unsupported detail just to make two labels different.\n\n"
    "OUTPUT FORMAT IS A STRICT CONTRACT. Return JSON Lines: exactly one complete "
    "JSON object per operation and no surrounding array, Markdown, header, or "
    "explanation or additional keys. Each non-empty line must match exactly: "
    "{\"id\":\"exact-operation-id\",\"label\":\"2-6 word label\"}. "
    "Return every supplied ID exactly once and do not invent IDs. Keeping records "
    "on independent lines allows valid records to be retained if another record "
    "is malformed."
)

_LABEL_OPSEQ_BATCH_RETRY_PROMPT = (
    _LABEL_OPSEQ_BATCH_SYSTEM_PROMPT
    + " This corrective request contains only unresolved operation IDs. Existing "
      "reservedLabels belong to accepted results and must not be repeated or "
      "rephrased. Return one valid JSON object line for every requested ID."
)

_PHASE_GATE_SYSTEM_PROMPT = (
    "For EVERY question, decide whether two adjacent groups of operations within "
    "one Java method belong to the same execution phase. An execution phase is "
    "a connected subprocess with one coherent operational purpose.\n\n"
    "Each question is independent. Decide it only from the evidence supplied for "
    "that question and assume no relationship between different questions.\n\n"
    "Each question contains leftGroup and rightGroup. Both groups contain a "
    "coreSignature and an ordered list of operations. The question also contains "
    "a systematicAssessment explaining why deterministic analysis could not "
    "resolve the boundary, together with its supporting, contradictory, and "
    "missing evidence.\n\n"
    "The names leftGroup and rightGroup describe the input structure only. They "
    "do not imply that the groups should remain separate.\n\n"
    "Return MERGE when leftGroup and rightGroup collectively perform one coherent "
    "subprocess. Return SPLIT when they perform genuinely different operational "
    "responsibilities or represent a clear handoff between responsibilities.\n\n"
    "systematicAssessment.directFlowAcrossBoundary indicates that data produced "
    "by leftGroup is consumed by rightGroup. Treat this as evidence of "
    "computational continuity supporting MERGE, unless the semantic evidence "
    "clearly indicates a handoff between different responsibilities.\n\n"
    "Use boundaryKind only as control-flow context. Do not treat the existence of "
    "a structural or operation boundary as evidence that the groups must be "
    "split. Answer every supplied question exactly once.\n\n"
    "HARD OUTPUT CONTRACT: return JSON Lines with exactly one complete JSON "
    "object per question and no surrounding array, Markdown, explanation, blank "
    "lines, or additional keys. Each object must contain exactly: id, copied "
    "unchanged from the supplied question; action, either \"MERGE\" or \"SPLIT\"; "
    "and confidence, a number from 0.0 to 1.0 expressing confidence in the "
    "selected action. Each non-empty line must follow this example: "
    '{"id":"q-1","action":"MERGE","confidence":0.85}'
)

_PHASE_GATE_RETRY_PROMPT = (
    _PHASE_GATE_SYSTEM_PROMPT
    + " This corrective request contains only questions whose previous records "
      "were missing or invalid. Return one valid JSON object line per ID."
)

_LABEL_METHOD_PHASES_SYSTEM_PROMPT = (
    "Label each already-grouped Java execution-phase subject with a specific "
    "2-6 word subprocess label. Count words as whitespace-separated tokens: "
    "every label must contain at least 2 and no more than 6 tokens. Dotted "
    "Java names such as Runtime.exec count as one token. Each subject is an "
    "independent question. Copy every subject ID exactly and completely. "
    "Use every phaseEvidence item within that subject together; multiple "
    "items mean the backend has determined that those execution phases must "
    "share one label. Treat each coreSignature together with its ordered "
    "operation callee and code context as the primary semantic evidence. "
    "Method identity provides "
    "scope. phaseIndex and localPhaseCount provide weak ordering context only: "
    "they may help distinguish setup, intermediate work, and finalization, "
    "but must never override the operation evidence or invent unsupported "
    "behaviour. Do not use unrelated subjects as evidence for one another. "
    "HARD OUTPUT CONTRACT: return JSON Lines with one complete object per subject "
    "and no surrounding array, Markdown, explanation, blank lines, or additional "
    "keys. Each non-empty line must match exactly: "
    '{"id":"subject-id","label":"2-6 word label"}. '
    "Labels may contain ampersand (&), slash (/), apostrophes, dots, and hyphens. "
    "Return no explanation, Markdown, or additional keys."
)

_LABEL_METHOD_PHASES_RETRY_PROMPT = (
    _LABEL_METHOD_PHASES_SYSTEM_PROMPT
    + " This corrective request contains only subjects whose previous records "
      "were missing or invalid. Return every requested ID exactly once."
)


# Payload builders live beside their response contracts so services cannot
# accidentally drift from the prompts they invoke.
def method_phase_label_payload(subjects, *, schema_version: str) -> dict:
    return {"schemaVersion": schema_version, "subjects": list(subjects)}


def execution_phase_gate_payload(questions) -> dict:
    return {
        "questions": [
            {"id": question.id, **question.to_prompt_payload()}
            for question in questions
        ]
    }


def topic_context_payload(cluster) -> dict[str, object]:
    display_label = cluster.llm_label or (
        cluster.statistical_terms[0] if cluster.statistical_terms else None
    )
    return {
        "id": cluster.label,
        "label": display_label,
        "representativeTerms": list(cluster.statistical_terms),
        "memberClasses": list(cluster.member_full_names),
    }


def operation_assignment_payload(
    operation_ids, operations_by_id, clusters
) -> dict[str, object]:
    from domain.operation_sequence.documents import operation_label_method_context

    contexts = {
        operation_id: operation_label_method_context(operations_by_id[operation_id])
        for operation_id in operation_ids
    }
    return {
        "topics": [
            topic_context_payload(cluster)
            for cluster in clusters
            if cluster.label != -1
        ],
        "operations": [
            {
                "id": operation_id,
                "entryPoint": contexts[operation_id][0],
                "subsequentMethods": contexts[operation_id][1],
            }
            for operation_id in operation_ids
        ],
    }


def operation_label_payload(
    operation_ids, operations_by_id, cluster, reserved_labels
) -> dict[str, object]:
    from domain.operation_sequence.documents import operation_label_method_context

    operation_context = {
        operation_id: operation_label_method_context(operations_by_id[operation_id])
        for operation_id in operation_ids
    }
    return {
        "topic": topic_context_payload(cluster) if cluster is not None else None,
        "reservedLabels": dict(reserved_labels),
        "operations": [
            {
                "id": operation_id,
                "entryPoint": operation_context[operation_id][0],
                "subsequentMethods": operation_context[operation_id][1],
            }
            for operation_id in operation_ids
        ],
    }


def cluster_label_payload(clusters, class_by_full_name=None) -> dict[str, object]:
    def values(items) -> str:
        return ", ".join(dict.fromkeys(
            value for item in items if (value := " ".join(str(item).split()))
        ))

    items = []
    for cluster in clusters:
        members = []
        for full_name in cluster.member_full_names:
            document = (
                class_by_full_name.get(full_name) if class_by_full_name else None
            )
            members.append({
                "class": document.className if document else full_name,
                "methods": values(document.methodNames).split(", ")
                if document and document.methodNames else [],
            })
        items.append({
            "id": str(cluster.label),
            "representativeTerms": list(cluster.statistical_terms),
            "members": members,
        })
    return {"clusters": items}


def whole_corpus_payload(class_documents, readme_documents) -> str:
    from domain.topic_discovery.documents import deduplicate_readme_documents
    from domain.topic_discovery.preprocessing import preprocess_document

    categories = (
        ("Methods", "methodNames", 400, "identifier"),
        ("Members", "memberNames", 240, "identifier"),
        ("Identifiers", "identifiers", 400, "identifier"),
        ("Comments", "comments", 240, "prose"),
        ("String literals", "literals", 240, "prose"),
    )

    def bounded(values, char_limit, evidence_type):
        selected, seen, used = [], set(), 0
        for raw_value in values:
            value = " ".join(str(raw_value).split())
            if not value:
                continue
            key = preprocess_document([value]) if evidence_type == "identifier" else value.casefold()
            if key in seen:
                continue
            seen.add(key)
            if len(value) > 160:
                value = value[:159].rstrip() + "…"
            separator = 2 if selected else 0
            remaining = char_limit - used - separator
            if remaining <= 0:
                break
            if len(value) > remaining:
                if remaining < 2:
                    break
                value = value[:remaining - 1].rstrip() + "…"
            selected.append(value)
            used += separator + len(value)
        return ", ".join(selected)

    lines = []
    for document in class_documents:
        lines.append(f"Class: {document.fullName}")
        for label, attribute, limit, evidence_type in categories:
            rendered = bounded(getattr(document, attribute), limit, evidence_type)
            if rendered:
                lines.append(f"  {label}: {rendered}")
    if readme_documents:
        lines.append("")
        lines.append("Project documentation:")
        for readme in deduplicate_readme_documents(readme_documents):
            lines.append(f"[{readme.package or 'root'}] {readme.text[:500]}")
    return "\n".join(lines)
