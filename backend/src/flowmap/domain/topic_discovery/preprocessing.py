from __future__ import annotations

import json
import re
from pathlib import Path

import nltk
from nltk.corpus import stopwords


_NOISE_PATTERNS = json.loads(
    (Path(__file__).parents[2] / "config" / "noise_patterns.json").read_text()
)
_ANGLE_BRACKET_MARKERS = tuple(_NOISE_PATTERNS["angle_bracket_markers"])

nltk.download("stopwords", quiet=True)
ENGLISH_STOPWORDS: set[str] = set(stopwords.words("english"))
MINIMUM_TOKEN_LENGTH = 3

_WORD_SPLIT_RE = re.compile(r"[^a-zA-Z0-9]+")
_CAMEL_SPLIT_RE = re.compile(
    r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])"
)


def split_identifier(term: str) -> list[str]:
    """Split source identifiers or prose into lowercase word fragments."""
    tokens: list[str] = []
    for word in _WORD_SPLIT_RE.split(term):
        if word:
            tokens.extend(_CAMEL_SPLIT_RE.split(word))
    return [token.lower() for token in tokens if token]


def preprocess_document(terms: list[str]) -> str:
    """Convert raw source-code terms into embedding-ready topic text."""
    tokens: list[str] = []
    for term in terms:
        if any(marker in term for marker in _ANGLE_BRACKET_MARKERS):
            continue
        for word in split_identifier(term):
            if (
                len(word) < MINIMUM_TOKEN_LENGTH
                or word in ENGLISH_STOPWORDS
            ):
                continue
            tokens.append(word)
    return " ".join(tokens)
