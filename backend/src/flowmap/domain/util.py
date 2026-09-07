import json
import re
from pathlib import Path

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


def is_lambda_method(method_name: str) -> bool:
    """Whether a method full name denotes a compiler-generated lambda body."""
    signature_free = method_name.split(":", 1)[0]
    method_part = signature_free.rsplit(".", 1)[-1]
    return _LAMBDA_RE.match(method_part) is not None


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
