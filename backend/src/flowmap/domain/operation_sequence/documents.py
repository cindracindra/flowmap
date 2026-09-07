from __future__ import annotations

from model import Graph, MethodDocument

from domain.topic_discovery.documents import (
    normalise_evidence_categories,
    normalise_identifier_evidence,
    normalise_prose_evidence,
)
from domain.topic_discovery.preprocessing import preprocess_document


def _method_evidence(
    document: MethodDocument,
) -> list[tuple[str, list[str]]]:
    categories = (
        ("Method", [document.methodName], normalise_identifier_evidence),
        ("Identifiers", document.identifiers, normalise_identifier_evidence),
        ("Comments", document.comments, normalise_prose_evidence),
        ("Messages", document.literals, normalise_prose_evidence),
    )
    return normalise_evidence_categories(categories)


def build_method_embedding_document(document: MethodDocument) -> str:
    """Build category-aware method text using class-document preprocessing."""
    return "\n".join(
        f"{label}: {'; '.join(values)}"
        for label, values in _method_evidence(document)
    )


def operation_method_names(operation: Graph) -> list[str]:
    """Return ordered, distinct fully qualified method names for an operation."""
    names: list[str] = []
    seen: set[str] = set()
    if operation.entryPoint:
        names.append(operation.entryPoint)
        seen.add(operation.entryPoint)
    for node in operation.nodes:
        full_name = node.calleeFullName
        if node.type != "entry" or not full_name or full_name in seen:
            continue
        seen.add(full_name)
        names.append(full_name)
    return names


def compact_method_name(full_name: str) -> str:
    """Reduce a Joern method name to ``Class.method`` without its signature."""
    name_without_signature = full_name.split(":", 1)[0]
    parts = name_without_signature.rsplit(".", 2)
    return ".".join(parts[-2:]) if len(parts) >= 2 else name_without_signature


def operation_method_sequence(operation: Graph) -> list[str]:
    """Return the ordered operation path as compact ``Class.method`` names."""
    sequence: list[str] = []
    seen: set[str] = set()
    for full_name in operation_method_names(operation):
        compact_name = compact_method_name(full_name)
        if compact_name in seen:
            continue
        seen.add(compact_name)
        sequence.append(compact_name)
    return sequence


def operation_label_method_context(
    operation: Graph,
) -> tuple[str | None, list[str]]:
    """Separate the initiating method from the ordered methods after it."""
    entry_point = (
        compact_method_name(operation.entryPoint)
        if operation.entryPoint
        else None
    )
    sequence = operation_method_sequence(operation)
    if entry_point is not None and sequence and sequence[0] == entry_point:
        sequence = sequence[1:]
    return entry_point, sequence


def build_operation_document(
    operation: Graph,
    method_documents: list[MethodDocument],
) -> str:
    """Build class-equivalent category evidence for an operation sequence."""
    method_by_full_name = {
        document.fullName: document for document in method_documents
    }
    method_names: list[str] = []
    identifiers: list[str] = []
    comments: list[str] = []
    literals: list[str] = []
    for full_name in operation_method_names(operation):
        document = method_by_full_name.get(full_name)
        if document is not None:
            method_names.append(document.methodName)
            identifiers.extend(document.identifiers)
            comments.extend(document.comments)
            literals.extend(document.literals)
        else:
            method_names.append(full_name)

    categories = (
        ("Methods", method_names, normalise_identifier_evidence),
        ("Identifiers", identifiers, normalise_identifier_evidence),
        ("Comments", comments, normalise_prose_evidence),
        ("Messages", literals, normalise_prose_evidence),
    )
    evidence = normalise_evidence_categories(categories)
    return "\n".join(
        f"{label}: {'; '.join(values)}" for label, values in evidence
    )
