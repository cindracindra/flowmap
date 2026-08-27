"""Shared normalization and validation for generated phase labels."""

from __future__ import annotations

import re


# Dotted Java identifiers such as Runtime.exec are one semantic label token,
# just like hyphenated and possessive words. Keeping the dot inside LABEL_WORD
# also makes the validator's count agree with the prompt's whitespace-based
# definition of a 2-6 word label.
LABEL_WORD = r"[^\W_]+(?:['’.\-\u2010-\u2015][^\W_]+)*"
LABEL_CONNECTOR = r"(?:&|/|-|\u2010|\u2011|\u2012|\u2013|\u2014|\u2015)"
PHASE_LABEL_RE = re.compile(
    rf"^{LABEL_WORD}(?:(?:\s+|\s+{LABEL_CONNECTOR}\s+){LABEL_WORD})+$"
)
LABEL_WORD_RE = re.compile(LABEL_WORD)


def normalise_phase_label(response: str) -> str:
    """Remove harmless wrappers while preserving meaningful punctuation."""
    label = response.strip()
    if len(label) >= 2 and (label[0], label[-1]) in {
        ('"', '"'), ("'", "'"), ("`", "`"), ("“", "”"), ("‘", "’"),
    }:
        label = label[1:-1].strip()
    return re.sub(r"\s+", " ", label)


def valid_phase_label(label: str) -> bool:
    return bool(
        PHASE_LABEL_RE.fullmatch(label)
        and 2 <= len(LABEL_WORD_RE.findall(label)) <= 6
    )
