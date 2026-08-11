import json
import re
import numpy as np
from pathlib import Path

import nltk
from nltk.corpus import stopwords
from sentence_transformers import SentenceTransformer

_NOISE_PATTERNS = json.loads(
    (Path(__file__).parent.parent / "config" / "noise_patterns.json").read_text()
)

_CONTAINS_NOISE = _NOISE_PATTERNS["call_site_noise"]["contains"]
_SYNTHETIC = _NOISE_PATTERNS["call_site_noise"]["synthetic"]
_ACCESSOR_PREFIX = _SYNTHETIC["accessor_prefix"]

_LAMBDA_RE = re.compile(_SYNTHETIC["lambda_infix_regex"])
_ANON_CLASS_RE = re.compile(_SYNTHETIC["anonymous_class_suffix_regex"])

_JDK_LEAF_OMIT_PREFIXES = tuple(_NOISE_PATTERNS["jdk_leaf_omit_prefixes"])
_JDK_CALL_SITE_STRIP_PREFIXES = tuple(_NOISE_PATTERNS["jdk_call_site_strip_prefixes"])

_ANGLE_BRACKET_MARKERS = tuple(_NOISE_PATTERNS["angle_bracket_markers"])


nltk.download("stopwords", quiet=True)
_EN_STOPS: set[str] = set(stopwords.words("english"))

# Short tokens that survive stop-word removal but carry no topic signal.
_SHORT_MIN = 3

# Splits a raw term into words first (on any non-alphanumeric run --
# underscores, punctuation, whitespace inside a comment/literal), then
# each word into camelCase/acronym boundaries.
# Two alternatives, both zero-width lookaround so nothing is consumed:
#   (?<=[a-z0-9])(?=[A-Z])       -- "getUserById" -> get|User|By|Id
#   (?<=[A-Z])(?=[A-Z][a-z])     -- "HTTPServer" -> HTTP|Server (keeps a
#                                    leading acronym run intact, splits
#                                    only right before the word that
#                                    follows it)
_WORD_SPLIT_RE = re.compile(r"[^a-zA-Z0-9]+")
_CAMEL_SPLIT_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")

def is_noise(method_name: str) -> bool:
    """
    Returns True if the method name is considered call-site noise and
    should be stripped (see noise_patterns.json's "call_site_noise").
    """
    if any(marker in method_name for marker in _CONTAINS_NOISE):
        return True

    signature_free = method_name.split(":", 1)[0]
    parts = signature_free.split(".")
    method_part = parts[-1]
    class_part = parts[-2] if len(parts) >= 2 else ""

    if method_part.startswith(_ACCESSOR_PREFIX):
        return True
    if _LAMBDA_RE.match(method_part):
        return True
    if _ANON_CLASS_RE.search(class_part):
        return True

    return False


def is_jdk_call_site_strip(method_name: str) -> bool:
    """
    Returns True if the method belongs to the JDK standard library and is
    considered a call-site noise that should be stripped.
    """
    return method_name.startswith(_JDK_CALL_SITE_STRIP_PREFIXES)


def is_jdk_builtin(method_name: str) -> bool:
    """
    Returns True if the method belongs to the JDK standard library and is
    considered a leaf node that should be omitted.
    """
    return method_name.startswith(_JDK_LEAF_OMIT_PREFIXES)


def split_identifier(term: str) -> list[str]:
    """
    Lowercased word fragments from one raw term (an identifier, or a
    whitespace-containing comment/literal string).
    """
    tokens: list[str] = []
    for word in _WORD_SPLIT_RE.split(term):
        if word:
            tokens.extend(_CAMEL_SPLIT_RE.split(word))
    return [t.lower() for t in tokens if t]


def preprocess_document(terms: list[str]) -> str:
    """
    Convert one Document's raw term list into a single preprocessed
    document string ready for embedding/vectorising.
    """
    tokens: list[str] = []
    for term in terms:
        if any(marker in term for marker in _ANGLE_BRACKET_MARKERS):
            continue
        for word in split_identifier(term):
            if len(word) < _SHORT_MIN or word in _EN_STOPS:
                continue
            tokens.append(word)
    return " ".join(tokens)


def embed_documents(
    texts: list[str], model_name: str = "all-MiniLM-L6-v2"
) -> np.ndarray:
    """
    Encode each preprocessed document with a local sentence-transformers
    model.
    """
    model = SentenceTransformer(model_name)
    embeddings = model.encode(texts, normalize_embeddings=True)
    return np.asarray(embeddings)
