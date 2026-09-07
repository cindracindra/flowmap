from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from model import ClassDocument, ReadmeDocument

from .preprocessing import preprocess_document


def normalise_identifier_evidence(value: str) -> str:
    return preprocess_document([value])


def normalise_prose_evidence(value: str) -> str:
    return " ".join(value.split())


def normalise_evidence_values(
    values: list[str], normalise: Callable[[str], str]
) -> list[str]:
    """Normalize and deduplicate evidence values while preserving order."""
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalised = normalise(value)
        if normalised and normalised not in seen:
            seen.add(normalised)
            result.append(normalised)
    return result


def normalise_evidence_categories(
    categories: tuple[
        tuple[str, list[str], Callable[[str], str]], ...
    ],
) -> list[tuple[str, list[str]]]:
    """Normalize and deduplicate every category using one shared policy."""
    evidence: list[tuple[str, list[str]]] = []
    for label, values, normalise in categories:
        rendered = normalise_evidence_values(values, normalise)
        if rendered:
            evidence.append((label, rendered))
    return evidence


def _class_evidence(
    document: ClassDocument,
) -> list[tuple[str, list[str]]]:
    categories = (
        ("Class", [document.className], normalise_identifier_evidence),
        ("Methods", document.methodNames, normalise_identifier_evidence),
        ("Members", document.memberNames, normalise_identifier_evidence),
        ("Identifiers", document.identifiers, normalise_identifier_evidence),
        ("Comments", document.comments, normalise_prose_evidence),
        ("Messages", document.literals, normalise_prose_evidence),
    )
    return normalise_evidence_categories(categories)


def build_class_embedding_document(document: ClassDocument) -> str:
    """Build category-aware class text for the embedding model."""
    return "\n".join(
        f"{label}: {'; '.join(values)}"
        for label, values in _class_evidence(document)
    )


def build_class_term_document(document: ClassDocument) -> str:
    """Build header-free class text for c-TF-IDF term extraction."""
    return " ".join(
        value
        for _, values in _class_evidence(document)
        for value in values
    )


def prepare_class_documents(
    class_documents: list[ClassDocument],
) -> tuple[list[ClassDocument], list[str], list[str]]:
    """Return aligned classes, structured embedding text, and term-only text."""
    prepared = [
        (
            document,
            build_class_embedding_document(document),
            build_class_term_document(document),
        )
        for document in class_documents
    ]
    kept = [row for row in prepared if row[1].strip()]
    if not kept:
        return [], [], []
    classes, embedding_texts, term_texts = zip(*kept)
    return list(classes), list(embedding_texts), list(term_texts)


def _is_within(candidate_dir: Path, base_dir: Path) -> bool:
    return candidate_dir == base_dir or base_dir in candidate_dir.parents


def _common_package_prefix(packages: list[str]) -> str:
    if not packages:
        return ""
    split_packages = [package.split(".") if package else [] for package in packages]
    prefix: list[str] = []
    for parts in zip(*split_packages):
        if len(set(parts)) != 1:
            break
        prefix.append(parts[0])
    return ".".join(prefix)


def _readme_preference(document: ReadmeDocument) -> tuple[int, str]:
    """Prefer a project-level document when duplicate content is found."""
    path = Path(document.path)
    return len(path.parts), document.path.casefold()


def deduplicate_readme_documents(
    documents: list[ReadmeDocument],
) -> list[ReadmeDocument]:
    """Keep one README per whitespace- and case-normalised full document."""
    selected: list[ReadmeDocument] = []
    seen_content: set[str] = set()
    for document in sorted(documents, key=_readme_preference):
        comparison_text = " ".join(document.text.split()).casefold()
        if comparison_text in seen_content:
            continue
        seen_content.add(comparison_text)
        selected.append(document)
    return selected


def extract_readme_documents(
    source_root: Path, class_documents: list[ClassDocument]
) -> list[ReadmeDocument]:
    """Find project markdown and associate it with the nearest Java package."""
    class_dirs = [
        (Path(document.filename).parent, document.package)
        for document in class_documents
    ]
    documents: list[ReadmeDocument] = []
    for path in sorted(source_root.rglob("*")):
        if not path.is_file():
            continue
        if not (
            path.name.upper().startswith("README")
            or path.name.lower().endswith(".md")
        ):
            continue
        relative_dir = path.parent.relative_to(source_root)
        packages = [
            package
            for class_dir, package in class_dirs
            if _is_within(class_dir, relative_dir)
        ]
        documents.append(
            ReadmeDocument(
                path=str(path.relative_to(source_root)),
                package=_common_package_prefix(packages),
                text=path.read_text(errors="ignore"),
            )
        )
    return deduplicate_readme_documents(documents)
